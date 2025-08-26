from __future__ import annotations

import asyncio
from typing import Iterable, Optional, Set

from fastapi import FastAPI, WebSocket
from starlette.endpoints import WebSocketEndpoint


def register_ws_routes(
    app: FastAPI,
    *,
    broadcast,
    channels_to_forward: Iterable[str],
    input_channel: str,
    clients_set: Optional[Set[WebSocket]] = None,
) -> None:
    """
    Register a WebSocket route that bridges pub/sub channels to the socket.

    - broadcast: object with publish(channel, message) and subscribe(channel) async context manager
    - backlog_provider: returns pre-encoded payload strings to send on connect
    - channels_to_forward: channel names to forward from pub/sub to this socket
    - input_channel: channel name to publish all incoming client messages
    - clients_set: optional set to track connected websockets (add/remove)
    """

    class AppWS(WebSocketEndpoint):
        encoding = "text"

        async def on_connect(self, ws: WebSocket):
            await ws.accept()
            if clients_set is not None:
                clients_set.add(ws)

            self._forward_tasks = [
                asyncio.create_task(self._forward(ws, channel))
                for channel in channels_to_forward
            ]

        async def on_receive(self, ws: WebSocket, data: str):
            try:
                await broadcast.publish(input_channel, data)
            except Exception:
                pass

        async def on_disconnect(self, ws: WebSocket, close_code: int):
            if clients_set is not None:
                try:
                    clients_set.discard(ws)
                except Exception:
                    pass
            for t in getattr(self, "_forward_tasks", []):
                try:
                    t.cancel()
                except Exception:
                    pass

        async def _forward(self, ws: WebSocket, channel: str):
            try:
                async with broadcast.subscribe(channel) as subscriber:
                    async for event in subscriber:
                        try:
                            await ws.send_text(event.message)
                        except Exception:
                            break
            except Exception:
                return

    app.add_websocket_route("/ws", AppWS)
