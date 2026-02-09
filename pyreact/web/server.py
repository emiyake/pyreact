from __future__ import annotations

import asyncio
import itertools
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from urllib.parse import parse_qs
from pyreact.boot.app_runner import AppRunner
from pyreact.core.hook import HookContext
from .console import enable_web_print, disable_web_print
from .broadcast import InMemoryBroadcast
from .ansi import ansi_to_html
from .ws_endpoint import (
    register_ws_routes,
    ChannelName,
)
from .input_consumer import InputConsumer
from .templates import BASE_HTML


def create_fastapi_app(runner: AppRunner):
    """Create the FastAPI app with lifecycle via ``lifespan``.

    The server subscribes directly to ``MessageBuffer`` and ``NavService``
    for component output, and uses ``runner.forward()`` for user input.
    No bridge callbacks are needed on the AppRunner side.
    """
    app = FastAPI()
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    _server_msg_counter = itertools.count(1)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        server_loop = asyncio.get_running_loop()
        broadcast = InMemoryBroadcast()
        enable_web_print()

        # ---- helpers ------------------------------------------------
        async def _broadcast_stdout(text: str) -> None:
            if text.startswith("__MESSAGE__:"):
                message_json = text[12:]
                message_data = json.loads(message_json)
                await broadcast.publish(
                    ChannelName.MSG,
                    {"channel": "chat", "type": "message", "data": message_data},
                )
                return

            await broadcast.publish(
                ChannelName.STDOUT,
                {"channel": "logs", "type": "stdout", "data": ansi_to_html(text)},
            )

        # ---- direct subscriptions -----------------------------------
        # Subscribe to MessageBuffer (singleton) for component output.
        # The callback fires synchronously in the runner thread, so we
        # marshal to the server loop with run_coroutine_threadsafe.
        console = runner.get_message_buffer()

        def _on_console(text: str) -> None:
            try:
                asyncio.run_coroutine_threadsafe(_broadcast_stdout(text), server_loop)
            except Exception:
                pass

        console.subscribe(_on_console)

        # Subscribe to NavService for navigation events.
        navsvc = runner.get_nav_service()

        def _on_nav(path: str) -> None:
            try:
                asyncio.run_coroutine_threadsafe(
                    broadcast.publish(
                        ChannelName.NAV,
                        {"channel": "ui", "type": "nav", "data": path},
                    ),
                    server_loop,
                )
            except Exception:
                pass

        navsvc.subs.append(_on_nav)

        # ---- input handling -----------------------------------------
        async def _handle_input_message(msg: dict) -> None:
            t = msg.get("t")

            if t in ("hello", "nav"):
                path = msg.get("path", "/")
                raw_query = msg.get("query", "") or ""
                raw_fragment = msg.get("fragment", "") or ""
                query = {}

                if len(raw_query) > 0:
                    q = raw_query[1:] if raw_query.startswith("?") else raw_query
                    parsed_q = parse_qs(q)
                    query = {
                        k: (v[0] if isinstance(v, list) and v else "")
                        for k, v in parsed_q.items()
                    }

                fragment = (
                    raw_fragment[1:] if raw_fragment.startswith("#") else raw_fragment
                )

                # Replay buffered messages on connect so mount-time
                # messages (welcome, routes, etc.) reach the client.
                if t == "hello":
                    for chunk in list(console._chunks):
                        await _broadcast_stdout(chunk.rstrip("\n"))

                runner.nav(path, query=query, fragment=fragment)
                return

            if t == "submit":
                value = msg.get("v", "")
                await broadcast.publish(
                    ChannelName.MSG,
                    {
                        "channel": "chat",
                        "type": "message",
                        "data": {
                            "type": "message",
                            "msg_id": f"srv-{next(_server_msg_counter)}",
                            "text": value,
                            "sender": "user",
                            "message_type": "chat",
                            "timestamp": time.time(),
                        },
                    },
                )

                async def _run_forward():
                    try:
                        await asyncio.to_thread(runner, value)
                    except Exception:
                        pass

                asyncio.create_task(_run_forward())
                return

            if t == "debug":
                what = msg.get("what")
                if what == "tree":
                    runner.print_vnode_tree()
                elif what == "trace":
                    runner.print_render_trace()

        # ---- WebSocket routes & consumer ----------------------------
        register_ws_routes(
            app,
            broadcast=broadcast,
            channels_to_forward=[ChannelName.NAV, ChannelName.STDOUT, ChannelName.MSG],
            input_channel=ChannelName.INPUT,
        )

        input_consumer = InputConsumer(
            broadcast=broadcast,
            input_channel=ChannelName.INPUT,
            handle_message=_handle_input_message,
        )

        input_task = asyncio.create_task(input_consumer.run())

        try:
            yield
        finally:
            # Cleanup subscriptions
            try:
                console.unsubscribe(_on_console)
            except Exception:
                pass
            try:
                navsvc.subs.remove(_on_nav)
            except Exception:
                pass

            runner.shutdown()
            input_task.cancel()
            HookContext._services.clear()
            disable_web_print()

    app.router.lifespan_context = lifespan

    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204, media_type="image/x-icon")

    @app.get("/{full_path:path}")
    async def index(_request: Request):
        return HTMLResponse(BASE_HTML)

    return app, None
