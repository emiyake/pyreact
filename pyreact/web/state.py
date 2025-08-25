from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Set

from fastapi import WebSocket

from pyreact.core.hook import HookContext
from pyreact.web.broadcast import InMemoryBroadcast


@dataclass
class ServerState:
    """Mutable server state kept in one place to respect SRP.

    This removes the need for module-level globals and makes it easier to test.
    """

    root_ctx: HookContext
    broadcast: InMemoryBroadcast = field(default_factory=InMemoryBroadcast)
    clients: Set[WebSocket] = field(default_factory=set)
    latest_html: Optional[str] = None
    html_updated_event: asyncio.Event = field(default_factory=asyncio.Event)
    pending_path: Optional[str] = None
