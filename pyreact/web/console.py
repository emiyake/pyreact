from typing import Callable, List
import builtins

_original_print = builtins.print
_enabled = False

class ConsoleBuffer:
    """Accumulates all printed output and notifies subscribers (server push)."""
    def __init__(self) -> None:
        self._chunks: List[str] = []
        self._subs: List[Callable[[str], None]] = []

    def write(self, text: str) -> None:
        if not text:
            return
        self._chunks.append(text)
        for fn in list(self._subs):
            try:
                fn(text)
            except Exception:
                pass

    def dump(self) -> str:
        # Raw content (no escaping). SSR will handle escaping.
        return "".join(self._chunks)

    def subscribe(self, fn: Callable[[str], None]) -> None:
        if fn not in self._subs:
            self._subs.append(fn)

    def unsubscribe(self, fn: Callable[[str], None]) -> None:
        try:
            self._subs.remove(fn)
        except ValueError:
            pass

def enable_web_print(echo_to_server_stdout: bool = True) -> None:
    """Redirects ``print()`` to :class:`ConsoleBuffer` (optionally keeping server stdout)."""
    global _enabled
    if _enabled:
        return

    def web_print(*args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        text = sep.join("" if a is None else str(a) for a in args) + end

        # Late import to avoid cycles
        from pyreact.core.hook import HookContext
        cb = HookContext.get_service("console_buffer", ConsoleBuffer)
        cb.write(text)

        if echo_to_server_stdout:
            _original_print(*args, **kwargs)

    builtins.print = web_print
    _enabled = True

def disable_web_print() -> None:
    global _enabled
    if _enabled:
        builtins.print = _original_print
        _enabled = False
