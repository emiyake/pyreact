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
from pyreact.web.console import MessageBuffer


def _emit_text_and_submit(bus: InputBus, text: str) -> None:
    now = time.time()
    bus.emit({"type": "text", "value": text, "source": "term", "ts": now})
    bus.emit({"type": "submit", "value": text, "source": "term", "ts": now})


class AppRunner:
    """Background runner that manages the render loop and input dispatching.

    Usage:
        app = AppRunner(Boot)
        app.invoke("hello")
        ...
        app.shutdown()
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

        # Web bridge callbacks (set by server): executed from runner thread
        self._on_nav: Optional[Callable[[str], None]] = None
        self._nav_listener = None
        self._on_console: Optional[Callable[[str], None]] = None
        self._console_listener = None
        self._did_enable_web_print: bool = False

        self._thread.start()
        self._ready.wait()

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
                # Let at least one render cycle commit before returning
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

        # Enqueue the forward request to be processed by _loop_main so that
        # all renders happen in the same task/context where providers set
        # their ContextVars (DSPy, etc.).
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
        """Activate a forward request inside _loop_main's task context.

        Sets the ``_active_forward`` ContextVar and injects the input prop.
        The normal render loop in ``_loop_main`` will drain renders as usual;
        when a component calls ``hooks.use_result()``, the ForwardRequest's
        future resolves and we relay the result back to the caller via *bridge*.

        If the render tree stabilises without any component calling
        ``use_result()``, the bridge resolves with ``None`` so the caller
        is not blocked indefinitely.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        fwd = ForwardRequest(input_value=text, future=future)

        _active_forward.set(fwd)

        # Inject input as a prop on the root component
        self._root_ctx.props = {**self._root_ctx.props, "input": text}
        schedule_rerender(self._root_ctx, reason=f"forward({text!r})")

        def _on_done(f: asyncio.Future) -> None:
            _active_forward.set(None)
            try:
                bridge.set_result(f.result())
            except Exception as exc:
                bridge.set_exception(exc)

        future.add_done_callback(_on_done)

        # Idle watchdog: resolve with None when the tree settles without
        # any component calling use_result().
        #
        # Steps:
        #   1. Wait for the scheduled render to actually start (render_signal).
        #   2. Then wait for renders to finish (render_idle).
        #   3. After a brief settle period, check if fwd.pending was set
        #      (component still working → keep waiting via new cycle).
        #   4. If no use_result() and not pending → resolve with None.
        async def _idle_watchdog() -> None:
            idle = get_render_idle()
            signal = get_render_signal()
            max_cycles = 60  # safety cap: ~30s total

            for _ in range(max_cycles):
                if future.done():
                    return

                # Wait for a render to be scheduled (schedule_rerender sets this)
                try:
                    await asyncio.wait_for(signal.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

                if future.done():
                    return

                # Wait for renders to complete
                try:
                    await asyncio.wait_for(idle.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass

                if future.done():
                    return

                await asyncio.sleep(0.3)

                if future.done():
                    return

                # If a component signalled it is working, restart the cycle
                if fwd.pending:
                    fwd.pending = False
                    continue

                # Tree is idle and nobody is pending → resolve with None
                if idle.is_set() and not signal.is_set():
                    break

            if not future.done():
                future.set_result(None)

        loop.create_task(_idle_watchdog())

    def __call__(self, text: str, **kwargs) -> Any:
        """DSPy-style invocation: ``result = app("question")``."""
        return self.forward(text, **kwargs)

    def shutdown(self) -> None:
        """Stop render loop and background threads."""
        if self._stopping:
            return
        self._stopping = True

        # Remove console buffer listener
        try:
            if self._console_listener is not None:
                console = HookContext.get_service("console_buffer", MessageBuffer)
                try:
                    console.unsubscribe(self._console_listener)
                except Exception:
                    pass
                self._console_listener = None
        except Exception:
            pass

        # Remove nav listener
        try:
            if self._nav_listener is not None:
                navsvc = self._root_ctx.get_service("nav_service", NavService)
                try:
                    navsvc.subs.remove(self._nav_listener)
                except Exception:
                    pass
                self._nav_listener = None
        except Exception:
            pass

        # Nudge the loop so the sleep wakes up promptly
        def _noop():
            return None

        try:
            self._loop.call_soon_threadsafe(_noop)
        except Exception:
            pass
        # Wait loop thread to exit
        self._thread.join(timeout=2.0)

    # -------------------------------
    # Debug/Navigation helpers
    # -------------------------------
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

    def attach_web_bridge(
        self,
        *,
        on_nav: Optional[Callable[[str], object]] = None,
        on_console: Optional[Callable[[str], object]] = None,
        target_loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        """Attach callbacks for HTML and navigation updates.
        If ``target_loop`` is provided, callbacks are marshaled to that loop:
        - coroutine callbacks → scheduled with asyncio.run_coroutine_threadsafe
        - regular callbacks → scheduled with loop.call_soon_threadsafe
        Otherwise, callbacks are invoked directly on the runner thread.
        """

        def _wrap(cb):
            if cb is None:
                return None
            if target_loop is None:
                return cb
            if inspect.iscoroutinefunction(cb):

                def _call(*args, **kwargs):
                    try:
                        asyncio.run_coroutine_threadsafe(
                            cb(*args, **kwargs), target_loop
                        )
                    except Exception:
                        pass

                return _call
            else:

                def _call(*args, **kwargs):
                    try:
                        target_loop.call_soon_threadsafe(cb, *args, **kwargs)
                    except Exception:
                        pass

                return _call

        self._on_nav = _wrap(on_nav)
        self._on_console = _wrap(on_console)

        # if self._on_console is not None:
        #     enable_web_print()

    # -------------------------------
    # Internal: loop thread
    # -------------------------------
    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._loop_main())

    async def _loop_main(self) -> None:
        from pyreact.core.debug import enable_tracing, clear_traces

        clear_traces()
        enable_tracing()

        # Initialize root context and services within the loop thread
        self._root_ctx = HookContext(
            self._app_component_fn.__name__, self._app_component_fn
        )
        schedule_rerender(self._root_ctx, reason="app startup")
        self._bus = self._root_ctx.get_service("input_bus", InputBus)

        # Bridge navigation events to server publisher
        try:
            navsvc = self._root_ctx.get_service("nav_service", NavService)

            def _nav_listener(path: str) -> None:
                try:
                    if self._on_nav is not None:
                        self._on_nav(path)
                except Exception:
                    pass

            navsvc.subs.append(_nav_listener)
            self._nav_listener = _nav_listener
        except Exception:
            pass

        # Bridge console buffer appends to server publisher
        try:
            console = HookContext.get_service("console_buffer", MessageBuffer)

            def _console_listener(text: str) -> None:
                try:
                    if self._on_console is not None:
                        self._on_console(text)
                except Exception:
                    pass

            console.subscribe(_console_listener)
            self._console_listener = _console_listener
        except Exception:
            pass

        # Run the first render so providers (DSPy, etc.) set their ContextVars.
        await run_renders()

        self._ready.set()

        interval = 1.0 / max(1, self._fps)
        try:
            while not self._stopping:
                # Activate any pending forward requests (non-blocking).
                # The result will be relayed when use_result() fires.
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
