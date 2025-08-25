from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from pyreact.core.runtime import get_render_signal, run_renders
from pyreact.web.state import ServerState
from pyreact.web.renderer import render_to_html


class RenderLoop:
    """Event-driven render loop. Waits on the global render signal and publishes HTML when changed.

    Dependencies are injected to improve testability and separation of concerns.
    """

    def __init__(
        self,
        state: ServerState,
        publish_html: Callable[[str], Awaitable[None]],
        navigate: Callable[[str], Awaitable[None]],
    ) -> None:
        self._state = state
        self._publish_html = publish_html
        self._navigate = navigate
        self._prev_html: Optional[str] = None

    async def run(self) -> None:
        signal = get_render_signal()
        while True:
            if not self._state.pending_path and not signal.is_set():
                await signal.wait()

            if self._state.pending_path:
                await self._navigate(self._state.pending_path)

            await run_renders()
            html_now = render_to_html(self._state.root_ctx)
            if html_now != self._prev_html:
                self._prev_html = html_now
                self._state.latest_html = html_now
                self._state.html_updated_event.set()
                await self._publish_html(html_now)
