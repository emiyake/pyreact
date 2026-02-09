import asyncio
import concurrent.futures
import inspect
import threading
import time
from typing import Any, Optional, Callable

from pyreact.core.hook import HookContext
from pyreact.core.forward import ForwardRequest, _active_forward
from pyreact.core.runtime import (
    run_renders,
    schedule_rerender,
    get_render_idle,
    get_render_signal,
)
from pyreact.input.bus import InputBus
from pyreact.router.nav_service import NavService
from pyreact.core import MessageBuffer


def _emit_text_and_submit(bus: InputBus, text: str) -> None:
    now = time.time()
    bus.emit({"type": "text", "value": text, "source": "term", "ts": now})
    bus.emit({"type": "submit", "value": text, "source": "term", "ts": now})


class AppRunner:
    """Background runner that manages the render loop and input dispatching.

    Usage::

        app = AppRunner(Boot)
        result = app("hello")          # forward / DSPy-style
        app.invoke("hello")            # fire-and-forget via InputBus
        app.nav("/about")              # programmatic navigation
        app.shutdown()

    External consumers (e.g. a web server) can subscribe to output
    via the singleton ``MessageBuffer()`` and ``NavService`` directly,
    without needing any bridge callbacks.
    """

    def __init__(self, app_component_fn, *, fps: int = 20):
        self._app_component_fn = app_component_fn
        self._fps: int = max(1, int(fps))
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread: threading.Thread = threading.Thread(
            target=self._thread_main, name="pyreact-app-loop", daemon=True
        )
        self._stopping: bool = False
        self._ready: threading.Event = threading.Event()

        self._root_ctx: Optional[HookContext] = None
        # Queue of pending forward requests to be processed by _loop_main.
        # Each item is (text, concurrent.futures.Future).
        self._forward_queue: asyncio.Queue = asyncio.Queue()

        self._thread.start()
        self._ready.wait()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def invoke(
        self, text: str, *, wait: bool = False, timeout: Optional[float] = None
    ) -> None:
        """Send a line of input to the app via the InputBus.

        If wait=True, blocks until the current render batch becomes idle or until timeout.
        """
        if self._stopping or self._bus is None:
            return

        async def _do_emit_and_maybe_wait(txt: str, should_wait: bool):
            _emit_text_and_submit(self._bus, txt)
            if should_wait:
                await asyncio.sleep(0)
                await get_render_idle().wait()

        fut = asyncio.run_coroutine_threadsafe(
            _do_emit_and_maybe_wait(text, wait), self._loop
        )
        if wait:
            try:
                fut.result(timeout=timeout)
            except Exception:
                return

    def forward(self, text: str, *, timeout: Optional[float] = 30.0) -> Any:
        """Send input and block until a component calls ``hooks.use_result()``.

        This is the DSPy-style invocation::

            result = app("What is 2+2?")

        The input is injected as the ``input`` prop on the root component.
        The reactive tree renders normally (hooks, effects, re-renders).
        When any component calls ``hooks.use_result(value)``, this method
        returns that value.

        Args:
            text: The user input to send.
            timeout: Max seconds to wait (default 30). ``None`` = wait forever.

        Returns:
            The value passed to ``hooks.use_result()`` by a component.

        Raises:
            TimeoutError: If no ``use_result()`` is called within *timeout*.
            RuntimeError: If the runner is shutting down.
        """
        if self._stopping:
            raise RuntimeError("AppRunner is shutting down")

        bridge: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(
            self._forward_queue.put_nowait, (text, bridge)
        )

        try:
            return bridge.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"forward() timed out after {timeout}s. "
                "No component called hooks.use_result()."
            )

    def _start_forward(self, text: str, bridge: concurrent.futures.Future) -> None:
        """Activate a forward request inside _loop_main's task context."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        fwd = ForwardRequest(input_value=text, future=future)

        _active_forward.set(fwd)

        self._root_ctx.props = {**self._root_ctx.props, "input": text}
        schedule_rerender(self._root_ctx, reason=f"forward({text!r})")

        def _on_done(f: asyncio.Future) -> None:
            _active_forward.set(None)
            self._clear_input()
            try:
                bridge.set_result(f.result())
            except Exception as exc:
                bridge.set_exception(exc)

        future.add_done_callback(_on_done)

        async def _idle_watchdog() -> None:
            idle = get_render_idle()
            signal = get_render_signal()
            max_cycles = 60

            for _ in range(max_cycles):
                if future.done():
                    return

                try:
                    await asyncio.wait_for(signal.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

                if future.done():
                    return

                try:
                    await asyncio.wait_for(idle.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass

                if future.done():
                    return

                await asyncio.sleep(0.3)

                if future.done():
                    return

                if fwd.pending:
                    fwd.pending = False
                    continue

                if idle.is_set() and not signal.is_set():
                    break

            if not future.done():
                future.set_result(None)

        loop.create_task(_idle_watchdog())

    def __call__(self, text: str, **kwargs) -> Any:
        """DSPy-style invocation: ``result = app("question")``."""
        return self.forward(text, **kwargs)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Stop render loop and background threads."""
        if self._stopping:
            return
        self._stopping = True

        def _noop():
            return None

        try:
            self._loop.call_soon_threadsafe(_noop)
        except Exception:
            pass
        self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Service access (for external consumers like the web server)
    # ------------------------------------------------------------------
    def get_nav_service(self) -> NavService:
        """Return the NavService instance used by the Router."""
        return self._root_ctx.get_service("nav_service", NavService)

    def get_message_buffer(self) -> MessageBuffer:
        """Return the singleton MessageBuffer."""
        return MessageBuffer()

    # ------------------------------------------------------------------
    # Debug / Navigation helpers
    # ------------------------------------------------------------------
    def print_vnode_tree(self) -> None:
        if self._stopping or self._root_ctx is None:
            return

        async def _task():
            self._root_ctx.render_tree()

        fut = asyncio.run_coroutine_threadsafe(_task(), self._loop)
        try:
            fut.result(timeout=1.0)
        except Exception:
            pass

    def print_render_trace(self) -> None:
        if self._stopping:
            return

        async def _task():
            try:
                from pyreact.core.debug import print_last_trace

                print_last_trace()
            except Exception:
                print("\x1b[90m[debug]\x1b[0m Render trace not available.")

        fut = asyncio.run_coroutine_threadsafe(_task(), self._loop)
        try:
            fut.result(timeout=1.0)
        except Exception:
            pass

    def nav(self, dest: str, query: Optional[dict] = None, fragment: str = "") -> None:
        if self._stopping or not dest:
            return

        navsvc = self._root_ctx.get_service("nav_service", NavService)
        go = getattr(navsvc, "navigate", None)
        if callable(go):
            if inspect.iscoroutinefunction(go):
                asyncio.run_coroutine_threadsafe(
                    go(dest, query=query, fragment=fragment), self._loop
                )
            else:

                def _call():
                    try:
                        go(dest, query=query, fragment=fragment)
                    except Exception:
                        pass

                try:
                    self._loop.call_soon_threadsafe(_call)
                except Exception:
                    pass

    def current_route(self) -> dict:
        if self._stopping:
            return {"path": "", "query": {}, "fragment": ""}

        async def _task():
            try:
                navsvc = self._root_ctx.get_service("nav_service", NavService)
                return {
                    "path": navsvc.get_path(),
                    "query": navsvc.get_query_params(),
                    "fragment": navsvc.get_fragment(),
                }
            except Exception:
                return {"path": "", "query": {}, "fragment": ""}

        fut = asyncio.run_coroutine_threadsafe(_task(), self._loop)
        try:
            return fut.result(timeout=1.0)
        except Exception:
            return {"path": "", "query": {}, "fragment": ""}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _clear_input(self) -> None:
        """Reset the root input prop to '' and schedule a re-render.

        Called automatically on navigation and on forward completion so that
        components on the new route don't see stale input.
        """
        if self._root_ctx is not None and self._root_ctx.props.get("input", "") != "":
            self._root_ctx.props = {**self._root_ctx.props, "input": ""}
            schedule_rerender(self._root_ctx, reason="input cleared (navigation)")

    # ------------------------------------------------------------------
    # Internal: loop thread
    # ------------------------------------------------------------------
    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._loop_main())

    async def _loop_main(self) -> None:
        from pyreact.core.debug import enable_tracing, clear_traces

        clear_traces()
        enable_tracing()

        self._root_ctx = HookContext(
            self._app_component_fn.__name__, self._app_component_fn
        )
        schedule_rerender(self._root_ctx, reason="app startup")
        self._bus = self._root_ctx.get_service("input_bus", InputBus)

        # Auto-clear input on navigation so new routes don't see stale input.
        _navsvc = self._root_ctx.get_service("nav_service", NavService)
        _navsvc.on_navigate = lambda _url: self._clear_input()

        # Run the first render so providers (DSPy, etc.) set their ContextVars.
        await run_renders()

        self._ready.set()

        interval = 1.0 / max(1, self._fps)
        try:
            while not self._stopping:
                while True:
                    try:
                        text, bridge = self._forward_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    self._start_forward(text, bridge)

                await run_renders()
                await asyncio.sleep(interval)
        finally:
            try:
                if self._root_ctx is not None:
                    self._root_ctx.unmount()
            except Exception:
                pass
