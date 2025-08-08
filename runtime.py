import asyncio
from typing import Optional


# Estruturas globais, mas criadas sob demanda no loop correto
# ------------------------------------------------------------
rerender_queue: asyncio.Queue = asyncio.Queue()
_enqueued: set = set()

_render_idle: Optional[asyncio.Event] = None   # será criado on-demand


def get_render_idle() -> asyncio.Event:
    """Garante que o Event pertence ao loop que estiver rodando agora."""
    global _render_idle
    if _render_idle is None:
        _render_idle = asyncio.Event()
        _render_idle.set()          # começa no estado "ocioso"
    return _render_idle


# API de scheduling / commit
# ------------------------------------------------------------
def schedule_rerender(ctx):
    loop = asyncio.get_running_loop()
    if ctx in _enqueued:
        return
    _enqueued.add(ctx)

    idle = get_render_idle()
    idle.clear()

    loop.call_soon_threadsafe(rerender_queue.put_nowait, ctx)


async def run_renders() -> None:
    from hook import HookContext   # import aqui para evitar loop infinito

    while not rerender_queue.empty():
        ctx: HookContext = await rerender_queue.get()
        _enqueued.discard(ctx)
        ctx.render()
        await ctx.run_effects()

        

    if rerender_queue.empty():
        get_render_idle().set()    # volta a ficar ocioso
        
