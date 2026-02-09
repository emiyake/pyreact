# forward.py ---------------------------------------------------
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ForwardRequest:
    """Represents a single forward() invocation awaiting a result."""

    input_value: Any
    future: asyncio.Future
    resolved: bool = False
    # Set to True when a component calls use_result(None) indicating it is
    # still working (loading).  The idle watchdog uses this to avoid
    # prematurely resolving the forward with None.
    pending: bool = False


# The currently active forward request, visible to the entire render tree.
# None means no forward is pending (the old invoke() path, or interactive mode).
_active_forward: ContextVar[Optional[ForwardRequest]] = ContextVar(
    "_active_forward", default=None
)
