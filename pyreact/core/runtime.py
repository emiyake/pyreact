# runtime.py -------------------------------------------------
import asyncio
from typing import Optional


# Global structures, created on demand in the correct loop
# ------------------------------------------------------------
rerender_queue: asyncio.Queue = asyncio.Queue()
_enqueued: set = set()

_render_idle: Optional[asyncio.Event] = None  # will be created on demand
_render_signal: Optional[asyncio.Event] = None  # set when a render is scheduled


def get_render_idle() -> asyncio.Event:
    """Ensure the ``Event`` belongs to the currently running loop."""
    global _render_idle
    if _render_idle is None:
        _render_idle = asyncio.Event()
        _render_idle.set()  # start in the 'idle' state
    return _render_idle


def get_render_signal() -> asyncio.Event:
    """Event that is set whenever a rerender is scheduled, and cleared after run_renders drains the queue.

    Useful for event-driven servers to await render activity instead of polling.
    """
    global _render_signal
    if _render_signal is None:
        _render_signal = asyncio.Event()
    return _render_signal


# scheduling / commit API
# ------------------------------------------------------------
def schedule_rerender(ctx):
    loop = asyncio.get_running_loop()
    if ctx in _enqueued:
        return
    _enqueued.add(ctx)

    def _enqueue():
        try:
            rerender_queue.put_nowait(ctx)
        finally:
            # Set the signal only after the ctx is in the queue to avoid races
            get_render_signal().set()

    loop.call_soon_threadsafe(_enqueue)


async def run_renders() -> None:
    from pyreact.core.hook import HookContext  # import here to avoid infinite loop

    # Drain the queue without awaiting on an initially-empty queue
    while True:
        try:
            ctx: HookContext = rerender_queue.get_nowait()
        except Exception:
            break
        _enqueued.discard(ctx)
        if getattr(ctx, "_mounted", True):
            ctx.render()
            await ctx.run_effects()

    # Mark idle and clear signal after draining current batch
    if rerender_queue.empty():
        get_render_idle().set()
        get_render_signal().clear()
