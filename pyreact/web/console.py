from __future__ import annotations

import sys
from collections import deque
from threading import RLock
from typing import Callable, Deque, List, Optional


__all__ = [
    "ConsoleBuffer",
    "enable_web_print",
    "disable_web_print",
]


# -----------------------------------------------------------------------------
# ConsoleBuffer (singleton) com ring buffer
# -----------------------------------------------------------------------------
class ConsoleBuffer:
    """
    Buffer de console com ring buffer por número de caracteres.

    - `append(text)` adiciona texto ao final e mantém o total <= max_chars.
    - `dump()` retorna todo o conteúdo atual (concatenação dos chunks).
    - `subscribe(cb)`/`unsubscribe(cb)` registram callbacks chamados em cada append.
    - Implementado como SINGLETON para ser compartilhado entre o servidor e o hook
      que intercepta stdout/stderr.
    """

    _instance: Optional["ConsoleBuffer"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_chars: int = 200_000) -> None:
        # Evitar re-inicialização no singleton
        if getattr(self, "_initialized", False):
            return

        self._max_chars: int = int(max_chars)
        self._chunks: Deque[str] = deque()
        self._length: int = 0
        self._subs: List[Callable[[str], None]] = []
        self._lock: RLock = RLock()

        self._initialized = True

    # ---------------- Ring buffer ops ----------------
    def _trim_left_until_within_limit(self) -> None:
        while self._length > self._max_chars and self._chunks:
            removed = self._chunks.popleft()
            self._length -= len(removed)

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._chunks.append(text)
            self._length += len(text)
            self._trim_left_until_within_limit()

        # Notificar assinantes FORA do lock, para evitar deadlocks
        for cb in list(self._subs):
            try:
                cb(text)
            except Exception:
                # Não interromper o fluxo por erro de callbacks
                pass

    # ---------------- API pública ----------------
    def dump(self) -> str:
        with self._lock:
            # join é O(n), mas amortizado e aceitável para páginas moderadas
            return "".join(self._chunks)

    def length(self) -> int:
        with self._lock:
            return self._length

    def subscribe(self, cb: Callable[[str], None]) -> None:
        with self._lock:
            if cb not in self._subs:
                self._subs.append(cb)

    def unsubscribe(self, cb: Callable[[str], None]) -> None:
        with self._lock:
            try:
                self._subs.remove(cb)
            except ValueError:
                pass

    def set_max_chars(self, max_chars: int) -> None:
        with self._lock:
            self._max_chars = int(max_chars)
            self._trim_left_until_within_limit()


# -----------------------------------------------------------------------------
# Redirecionamento de stdout/stderr
# -----------------------------------------------------------------------------
_original_stdout = None
_original_stderr = None
_patched = False


class _WebStream:
    """
    File-like que duplica writes:
      - opcionalmente ecoa no stream original (para logs do servidor)
      - sempre escreve no ConsoleBuffer singleton
    """

    def __init__(self, console: ConsoleBuffer, original, echo: bool) -> None:
        self._console = console
        self._original = original
        self._echo = bool(echo)
        # Expor encoding para compatibilidade com objetos de arquivo
        self.encoding = getattr(original, "encoding", "utf-8")

    def write(self, s: str) -> int:
        # Ecoar no original (se habilitado)
        if self._echo and self._original is not None:
            try:
                self._original.write(s)
            except Exception:
                # Ignore erros do stream original para não quebrar a captura
                pass
            try:
                self._original.flush()
            except Exception:
                pass

        # Sempre enviar ao buffer web
        try:
            self._console.append(s)
        except Exception:
            # Nunca deixar a aplicação cair por erro no buffer
            pass
        return len(s)

    def flush(self) -> None:
        if self._echo and self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    # Compatibilidade básica com APIs de arquivo
    def isatty(self) -> bool:  # type: ignore[override]
        return False

    def fileno(self) -> int:  # type: ignore[override]
        try:
            return self._original.fileno()  # type: ignore[attr-defined]
        except Exception:
            return 1


def enable_web_print(*, echo_to_server_stdout: bool = True, max_chars: Optional[int] = None) -> None:
    """
    Redireciona `sys.stdout` e `sys.stderr` para o ConsoleBuffer singleton,
    com opção de também escrever nos streams originais do servidor.

    - `echo_to_server_stdout=True`: mantém logs no terminal/uvicorn.
    - `max_chars`: opcionalmente altera o tamanho do ring buffer neste momento.

    Chamadas múltiplas são idempotentes.
    """
    global _original_stdout, _original_stderr, _patched

    if _patched:
        # Permitir reconfigurar o tamanho do buffer mesmo já estando ativo
        if max_chars is not None:
            ConsoleBuffer().set_max_chars(int(max_chars))
        return

    console = ConsoleBuffer()
    if max_chars is not None:
        console.set_max_chars(int(max_chars))

    _original_stdout = sys.stdout
    _original_stderr = sys.stderr

    sys.stdout = _WebStream(console, _original_stdout, echo_to_server_stdout)  # type: ignore[assignment]
    sys.stderr = _WebStream(console, _original_stderr, echo_to_server_stdout)  # type: ignore[assignment]

    _patched = True


def disable_web_print() -> None:
    """
    Restaura `sys.stdout` e `sys.stderr` originais.
    Silencioso se já estiver desabilitado.
    """
    global _original_stdout, _original_stderr, _patched

    if not _patched:
        return

    try:
        if _original_stdout is not None:
            sys.stdout = _original_stdout  # type: ignore[assignment]
    except Exception:
        pass

    try:
        if _original_stderr is not None:
            sys.stderr = _original_stderr  # type: ignore[assignment]
    except Exception:
        pass

    _original_stdout = None
    _original_stderr = None
    _patched = False