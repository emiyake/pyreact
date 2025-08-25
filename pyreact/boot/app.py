import asyncio
import threading
import time
from typing import Optional

from pyreact.core.hook import HookContext
from pyreact.core.runtime import run_renders, schedule_rerender, get_render_idle
from pyreact.input.bus import InputBus
from pyreact.web.console import ConsoleBuffer
import json


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

        # Set on loop thread during startup
        self._root_ctx: Optional[HookContext] = None
        self._bus: Optional[InputBus] = None

        # Message capture (from ConsoleBuffer "__MESSAGE__:" lines)
        self._msg_lock: threading.Lock = threading.Lock()
        self._messages: list[str] = []

        # Console subscription callback (set on loop thread)
        self._console_cb = None

        self._thread.start()
        # Wait until the loop thread initialized the app
        self._ready.wait()

    # -------------------------------
    # Public API
    # -------------------------------
    def invoke(
        self, text: str, *, wait: bool = False, timeout: Optional[float] = None
    ) -> str:
        """Send a line of input to the app via the InputBus.

        If wait=True, blocks until the current render batch becomes idle or until timeout.
        Returns formatted message lines (joined by newlines) captured during this invoke.
        """
        if self._stopping or self._bus is None:
            return ""

        # Snapshot the current message count to compute delta later
        with self._msg_lock:
            start_idx = len(self._messages)

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
                return ""
        # Collect delta messages and return as a single string
        with self._msg_lock:
            lines = self._messages[start_idx:]
        return "\n".join(lines)

    def shutdown(self) -> None:
        """Stop render loop and background threads."""
        if self._stopping:
            return
        self._stopping = True

        # Unsubscribe console buffer (best-effort)
        try:
            if self._console_cb is not None:
                ConsoleBuffer().unsubscribe(self._console_cb)
                self._console_cb = None
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
    # Internal: loop thread
    # -------------------------------
    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._loop_main())

    async def _loop_main(self) -> None:
        # Initialize root context and services within the loop thread
        self._root_ctx = HookContext(
            self._app_component_fn.__name__, self._app_component_fn
        )
        schedule_rerender(self._root_ctx, reason="app startup")
        self._bus = HookContext.get_service("input_bus", InputBus)

        # Subscribe to ConsoleBuffer for structured Message events
        console = ConsoleBuffer()

        def _on_console_append(chunk: str) -> None:
            try:
                # Fast path: check marker existence first
                if "__MESSAGE__:" not in chunk:
                    return
                for line in chunk.splitlines():
                    if not line.startswith("__MESSAGE__:"):
                        continue
                    payload = line[len("__MESSAGE__:") :]
                    try:
                        data = json.loads(payload)
                    except Exception:
                        continue
                    sender = str(data.get("sender", "").upper() or "MESSAGE")
                    text = str(data.get("text", ""))
                    formatted = f"[{sender}] {text}"
                    with self._msg_lock:
                        self._messages.append(formatted)
            except Exception:
                # Never break the app due to console parsing
                pass

        console.subscribe(_on_console_append)
        self._console_cb = _on_console_append

        # Signal readiness to callers
        self._ready.set()

        # Render loop
        interval = 1.0 / max(1, self._fps)
        try:
            while not self._stopping:
                await run_renders()
                await asyncio.sleep(interval)
        finally:
            # Graceful unmount
            try:
                if self._root_ctx is not None:
                    self._root_ctx.unmount()
            except Exception:
                pass


def run_app(app_component_fn, *, fps: int = 20) -> AppRunner:
    """Create and start an AppRunner for the given root component."""
    return AppRunner(app_component_fn, fps=fps)
