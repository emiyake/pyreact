import asyncio
import inspect
import threading
import time
from typing import Optional, Callable

from pyreact.core.hook import HookContext
from pyreact.core.runtime import run_renders, schedule_rerender, get_render_idle
from pyreact.input.bus import InputBus
from pyreact.web.nav_service import NavService


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

    def __init__(self, app_component_fn, *, fps: int = 20, trace: bool = True):
        self._app_component_fn = app_component_fn
        self._fps: int = max(1, int(fps))
        self._trace: bool = bool(trace)
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread: threading.Thread = threading.Thread(
            target=self._thread_main, name="pyreact-app-loop", daemon=True
        )
        self._stopping: bool = False
        self._ready: threading.Event = threading.Event()

        # Set on loop thread during startup
        self._root_ctx: Optional[HookContext] = None

        # Removed message buffering and console subscription

        # Web bridge callbacks (set by server): executed from runner thread
        self._on_nav: Optional[Callable[[str], None]] = None
        self._nav_listener = None

        self._thread.start()
        # Wait until the loop thread initialized the app
        self._ready.wait()

    # -------------------------------
    # Public API
    # -------------------------------
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

    def shutdown(self) -> None:
        """Stop render loop and background threads."""
        if self._stopping:
            return
        self._stopping = True

        # No console buffer to unsubscribe
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
            try:
                BOLD = "\x1b[1m"
                CYAN = "\x1b[36m"
                RESET = "\x1b[0m"
                print(f"\n{BOLD}{CYAN}=== VNode Tree ==={RESET}")
                self._root_ctx.render_tree()
                print(f"{BOLD}{CYAN}=================={RESET}\n")
            except Exception:
                print("\x1b[90m[debug]\x1b[0m VNode tree not available.")

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

    # -------------------------------
    # Internal: loop thread
    # -------------------------------
    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._loop_main())

    async def _loop_main(self) -> None:
        # Optionally enable tracing by default
        if self._trace:
            try:
                from pyreact.core.debug import enable_tracing, clear_traces

                clear_traces()
                enable_tracing()
            except Exception:
                pass

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

        # Removed ConsoleBuffer subscription

        # Signal readiness to callers
        self._ready.set()

        interval = 1.0 / max(1, self._fps)
        try:
            while not self._stopping:
                await run_renders()
                await asyncio.sleep(interval)
        finally:
            try:
                if self._root_ctx is not None:
                    self._root_ctx.unmount()
            except Exception:
                pass
