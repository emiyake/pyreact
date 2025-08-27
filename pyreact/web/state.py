from __future__ import annotations

from dataclasses import dataclass, field
from .broadcast import InMemoryBroadcast


@dataclass
class ServerState:
    broadcast: InMemoryBroadcast = field(default_factory=InMemoryBroadcast)
