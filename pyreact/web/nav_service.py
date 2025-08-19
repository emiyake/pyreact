from dataclasses import dataclass, field
from typing import Callable, Optional, Dict
from urllib.parse import urlparse, parse_qs


@dataclass
class NavService:
    subs: list[Callable[[str], None]] = field(default_factory=list)
    navigate: Optional[Callable[[str], None]] = None
    current: str = "/"

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def get_query_params(self) -> Dict[str, str]:
        """Get query parameters from current URL"""
        parsed = urlparse(self.current)
        query_params = {}

        # Parse query string into a flat dict (taking first value for each key)
        for key, values in parse_qs(parsed.query).items():
            query_params[key] = values[0] if values else ""

        return query_params

    def get_fragment(self) -> str:
        """Get fragment/hash from current URL"""
        parsed = urlparse(self.current)
        return parsed.fragment

    def get_path(self) -> str:
        """Get path portion of current URL (without query or fragment)"""
        parsed = urlparse(self.current)
        return parsed.path
