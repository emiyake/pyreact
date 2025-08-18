import asyncio
from pyreact.core.hook import HookContext
from pyreact.core.runtime import get_render_idle, run_renders, schedule_rerender
from pyreact.input.bus import InputBus
from pyreact.input.providers.terminal import TerminalInput

def run_terminal(app_component_fn, *, fps: int = 20, prompt: str = ">> "):
    async def _main():
        root = HookContext(app_component_fn.__name__, app_component_fn)
        schedule_rerender(root)

        bus = HookContext.get_service("input_bus", InputBus)
        ti = TerminalInput(bus, prompt=prompt)
        ti.start()

        
        try:
            interval = 1.0 / max(1, fps)
            while not ti._stopping:
                await run_renders()
                await asyncio.sleep(interval)
        except (KeyboardInterrupt, SystemExit):
            pass

    asyncio.run(_main())