# runtime.py -------------------------------------------------
import asyncio
from typing import Optional


# Global structures, created on demand in the correct loop
# ------------------------------------------------------------
rerender_queue: asyncio.Queue = asyncio.Queue()
_enqueued: set = set()

_render_idle: Optional[asyncio.Event] = None   # will be created on demand


def get_render_idle() -> asyncio.Event:
    """Ensure the ``Event`` belongs to the currently running loop."""
    global _render_idle
    if _render_idle is None:
        _render_idle = asyncio.Event()
        _render_idle.set()          # start in the 'idle' state
    return _render_idle


# scheduling / commit API
# ------------------------------------------------------------
def schedule_rerender(ctx):
    loop = asyncio.get_running_loop()
    if ctx in _enqueued:
        return
    _enqueued.add(ctx)

    # idle = get_render_idle()
    # idle.clear()

    loop.call_soon_threadsafe(rerender_queue.put_nowait, ctx)


async def run_renders() -> None:
    from pyreact.core.hook import HookContext   # import here to avoid infinite loop

    while not rerender_queue.empty():
        ctx: HookContext = await rerender_queue.get()
        _enqueued.discard(ctx)
        # Skip rendering if the context has been unmounted since it was enqueued
        if getattr(ctx, "_mounted", True):
            ctx.render()
            await ctx.run_effects()

    if rerender_queue.empty():        
        get_render_idle().set() 
        
