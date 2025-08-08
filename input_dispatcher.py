import asyncio
from weakref import WeakSet
from typing import Optional
from runtime import get_render_idle

class _InputDispatcher:
    def __init__(self) -> None:
        self._subs: WeakSet = WeakSet()
        self._task: Optional[asyncio.Task] = None

    async def _listen(self):
        loop = asyncio.get_event_loop()
        try:
            while self._subs:
                await asyncio.sleep(0.5)
                txt = await loop.run_in_executor(None, input, "⌨️ Type a key: ")
                for cb in list(self._subs):
                    try:
                        cb(txt.strip())
                    except Exception:
                        pass
                # espera commits terminarem no *mesmo* loop
                await get_render_idle().wait()
                
        finally:
            self._task = None

    def subscribe(self, cb):
        self._subs.add(cb)
        if self._task is None:
            self._task = asyncio.create_task(self._listen())


    def unsubscribe(self, cb):
        self._subs.discard(cb)
        # if not self._subs and self._task:
        #     self._task.cancel()