import asyncio
import time
from typing import Optional
from pyreact.input.bus import InputBus

def _emit_text_submit(bus: InputBus, txt: str):
    now = time.time()
    bus.emit({"type": "text", "value": txt, "source": "term", "ts": now})
    bus.emit({"type": "submit", "value": txt, "source": "term", "ts": now})

class TerminalInput:
    """
    Leitor de terminal simples (linha por linha).
    Para por-tecla, dá para implementar modo raw depois.
    """
    def __init__(self, bus: InputBus, prompt: str = ">> "):
        self.bus = bus
        self.prompt = prompt
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    async def _runner(self):
        loop = asyncio.get_running_loop()
        while not self._stopping:
            txt = await loop.run_in_executor(None, input, self.prompt)
            _emit_text_submit(self.bus, txt)
            if txt.strip() in ("exit", "quit"):
                self._stopping = True
                break

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._runner())

    def stop(self):
        self._stopping = True