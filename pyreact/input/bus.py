from typing import Callable, List, TypedDict, Literal

class Event(TypedDict, total=False):
    type: Literal["text", "submit", "key"]   # expanda se quiser
    value: str
    source: Literal["web", "term"]
    ts: float

Subscriber = Callable[[Event], None]

class InputBus:
    """Barramento de entrada (thread-safe o suficiente para o uso com asyncio)."""
    def __init__(self) -> None:
        self._subs: List[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> None:
        if fn not in self._subs:
            self._subs.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        try:
            self._subs.remove(fn)
        except ValueError:
            pass

    def emit(self, ev: Event) -> None:
        for fn in list(self._subs):
            try:
                fn(ev)
            except Exception:
                # não deixa um subscriber ruim matar os outros
                pass