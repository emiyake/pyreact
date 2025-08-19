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
# ConsoleBuffer (singleton) with ring buffer
# -----------------------------------------------------------------------------
class ConsoleBuffer:
    """
    Console buffer with a character-count ring buffer.

    - `append(text)` adds text to the end and keeps the total <= max_chars.
    - `dump()` returns the entire current content (concatenation of chunks).
    - `subscribe(cb)`/`unsubscribe(cb)` register callbacks invoked on each append.
    - Implemented as a SINGLETON to be shared between the server and the hook
      that intercepts stdout/stderr.
    """

    _instance: Optional["ConsoleBuffer"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_chars: int = 200_000) -> None:
        # Avoid re-initialization in the singleton
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

        # Notify subscribers OUTSIDE the lock to avoid deadlocks
        for cb in list(self._subs):
            try:
                cb(text)
            except Exception:
                # Do not break the flow due to subscriber errors
                pass

    # ---------------- Public API ----------------
    def dump(self) -> str:
        with self._lock:
            # join is O(n), but amortized and acceptable for moderate pages
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
# Redirection of stdout/stderr
# -----------------------------------------------------------------------------
_original_stdout = None
_original_stderr = None
_patched = False


class _WebStream:
    """
    File-like that duplicates writes:
      - optionally echoes to the original stream (for server logs)
      - always writes to the ConsoleBuffer singleton
    """

    def __init__(self, console: ConsoleBuffer, original, echo: bool) -> None:
        self._console = console
        self._original = original
        self._echo = bool(echo)
        # Expose encoding for compatibility with file-like objects
        self.encoding = getattr(original, "encoding", "utf-8")

    def write(self, s: str) -> int:
        # Echo to the original stream (if enabled)
        if self._echo and self._original is not None:
            try:
                self._original.write(s)
            except Exception:
                # Ignore errors from the original stream to avoid breaking capture
                pass
            try:
                self._original.flush()
            except Exception:
                pass

        # Always send to the web buffer
        try:
            self._console.append(s)
        except Exception:
            # Never let the application crash due to buffer errors
            pass
        return len(s)

    def flush(self) -> None:
        if self._echo and self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    # Basic compatibility with file-like APIs
    def isatty(self) -> bool:  # type: ignore[override]
        return False

    def fileno(self) -> int:  # type: ignore[override]
        try:
            return self._original.fileno()  # type: ignore[attr-defined]
        except Exception:
            return 1


def enable_web_print(*, echo_to_server_stdout: bool = True, max_chars: Optional[int] = None) -> None:
    """
    Redirects `sys.stdout` and `sys.stderr` to the ConsoleBuffer singleton,
    with an option to also write to the server's original streams.

    - `echo_to_server_stdout=True`: keeps logs in the terminal/uvicorn.
    - `max_chars`: optionally changes the ring buffer size at this time.

    Multiple calls are idempotent.
    """
    global _original_stdout, _original_stderr, _patched

    if _patched:
        # Allow reconfiguring the buffer size even when already active
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
    Restores the original `sys.stdout` and `sys.stderr`.
    No-op if already disabled.
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