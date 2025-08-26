from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager


from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from urllib.parse import parse_qs

from pyreact.core.hook import HookContext
from pyreact.web.console import (
    ConsoleBuffer,
    enable_web_print,
    disable_web_print,
    _original_stdout,
)
from pyreact.web.ansi import ansi_to_html
from pyreact.web.ws_endpoint import register_ws_routes
from pyreact.boot.bootstrap import bootstrap
from pyreact.web.state import ServerState
from pyreact.web.input_consumer import InputConsumer
from pyreact.web.templates import BASE_HTML


# Channel names
CHAN_HTML = "html"
CHAN_NAV = "nav"
CHAN_STDOUT = "stdout"
CHAN_MSG = "message"
CHAN_INPUT = "input"


def create_fastapi_app(app_component_fn=None, *, app_runner=None):
    """Create the FastAPI app with lifecycle via ``lifespan``.

    When ``app_runner`` is provided, the server bridges HTML/nav updates from the
    runner and forwards WebSocket inputs via runner.invoke/nav/print helpers.
    """
    app = FastAPI()
    # Serve static assets (JS/CSS) from the package's static directory
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ---------- lifespan (startup/shutdown) ----------
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 0. Clean up any residual global state
        HookContext._services.clear()

        # Create application state and runner
        server_loop = asyncio.get_running_loop()
        runner = app_runner or bootstrap(app_component_fn)
        state = ServerState()

        enable_web_print()
        console = HookContext.get_service("console_buffer", ConsoleBuffer)

        # Subscriber that pushes stdout to clients
        def _on_console(text: str):
            asyncio.create_task(_broadcast_stdout(text))

        console.subscribe(_on_console)

        async def publish_nav(path: str) -> None:
            payload = json.dumps({"channel": "ui", "type": "nav", "path": path})
            await state.broadcast.publish(CHAN_NAV, payload)

        try:
            runner.attach_web_bridge(
                on_nav=publish_nav,
                target_loop=server_loop,
            )
        except Exception:
            pass

        async def _broadcast_stdout(text: str) -> None:
            # Check if this is a special message format
            if text.startswith("__MESSAGE__:"):
                # Extract the JSON message data
                try:
                    message_json = text[12:]  # Remove "__MESSAGE__:" prefix
                    message_data = json.loads(message_json)

                    # Format for terminal output
                    sender_colors = {
                        "user": "\x1b[34m",  # Azul
                        "system": "\x1b[90m",  # Cinza
                        "assistant": "\x1b[32m",  # Verde
                    }
                    type_colors = {
                        "chat": "",
                        "info": "\x1b[36m",  # Ciano
                        "warning": "\x1b[33m",  # Amarelo
                        "error": "\x1b[31m",  # Vermelho
                    }

                    sender = message_data.get("sender", "user")
                    message_type = message_data.get("message_type", "chat")
                    message_text = message_data.get("text", "")

                    color = sender_colors.get(sender, "") + type_colors.get(
                        message_type, ""
                    )
                    reset = "\x1b[0m"

                    # Print to terminal (original stdout)
                    formatted_text = (
                        f"{color}[{sender.upper()}] {message_text}{reset}\n"
                    )
                    if _original_stdout:
                        _original_stdout.write(formatted_text)
                        _original_stdout.flush()

                    # Send as a special message type to web clients (chat channel)
                    payload = json.dumps(
                        {"channel": "chat", "type": "message", "data": message_data}
                    )

                except json.JSONDecodeError:
                    # Fallback to regular stdout if JSON parsing fails
                    payload = json.dumps(
                        {
                            "channel": "logs",
                            "type": "stdout",
                            "html": ansi_to_html(text),
                        }
                    )
            else:
                # Convert ANSI to HTML for colored output in browser
                payload = json.dumps(
                    {"channel": "logs", "type": "stdout", "html": ansi_to_html(text)}
                )
            try:
                if text.startswith("__MESSAGE__:"):
                    await state.broadcast.publish(CHAN_MSG, payload)
                    return
            except Exception:
                pass
            await state.broadcast.publish(CHAN_STDOUT, payload)

        async def _handle_input_message(msg: dict) -> None:
            t = msg.get("t")

            if t in ("hello", "nav"):
                path = msg.get("path", "/")
                raw_query = msg.get("query", "") or ""
                raw_fragment = msg.get("fragment", "") or ""

                # Normalize string forms that may include leading '?' or '#'
                if isinstance(raw_query, str):
                    q = raw_query[1:] if raw_query.startswith("?") else raw_query
                    parsed_q = parse_qs(q)
                    # Flatten values: keep only the first value per key
                    query = {
                        k: (v[0] if isinstance(v, list) and v else "")
                        for k, v in parsed_q.items()
                    }
                elif isinstance(raw_query, dict):
                    query = {str(k): str(v) for k, v in raw_query.items()}
                else:
                    query = {}

                fragment = (
                    raw_fragment[1:]
                    if isinstance(raw_fragment, str) and raw_fragment.startswith("#")
                    else str(raw_fragment or "")
                )

                runner.nav(path, query=query, fragment=fragment)
                return

            if t in ("text", "submit"):
                value = msg.get("v", "")

                if t == "submit" and value.strip():
                    user_message_data = {
                        "type": "message",
                        "text": value,
                        "sender": "user",
                        "message_type": "chat",
                        "timestamp": time.time(),
                    }
                    user_payload = json.dumps(
                        {
                            "channel": "chat",
                            "type": "message",
                            "data": user_message_data,
                        }
                    )
                    await state.broadcast.publish(CHAN_MSG, user_payload)
                    runner.invoke(value, wait=True)
                    return

            if t == "debug":
                what = msg.get("what")
                if what == "tree":
                    runner.print_vnode_tree()
                elif what == "trace":
                    runner.print_render_trace()

        input_consumer = InputConsumer(
            broadcast=state.broadcast,
            input_channel=CHAN_INPUT,
            handle_message=_handle_input_message,
        )

        input_task = asyncio.create_task(input_consumer.run())

        # Store state in app for access by routes
        app.state.server_state = state
        app.state.app_runner = runner

        # Register WebSocket routes
        register_ws_routes(
            app,
            broadcast=state.broadcast,
            channels_to_forward=[CHAN_HTML, CHAN_NAV, CHAN_STDOUT, CHAN_MSG],
            input_channel=CHAN_INPUT,
            clients_set=state.clients,
        )

        # Initial SSR will already use stdout accumulated so far
        app.state._cleanup = {
            "console": console,
            "console_listener": _on_console,
            "render_task": None,
            "input_task": input_task,
        }

        try:
            yield
        finally:
            console.unsubscribe(_on_console)

            if app.state._cleanup.get("render_task") is not None:
                app.state._cleanup.get("render_task").cancel()

            runner.shutdown()
            input_task.cancel()
            HookContext._services.clear()
            disable_web_print()

    app.router.lifespan_context = lifespan

    # ---------- routes ----------
    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204, media_type="image/x-icon")

    @app.get("/{full_path:path}")
    async def index(request: Request, full_path: str = ""):
        console = HookContext.get_service("console_buffer", ConsoleBuffer)
        console.clear()

        accept = request.headers.get("accept", "")

        # Only skip SSR for specific asset types, not for missing Accept header
        if (
            accept
            and "text/html" not in accept.lower()
            and any(
                asset_type in accept.lower()
                for asset_type in [
                    "image/",
                    "text/css",
                    "application/javascript",
                    "font/",
                ]
            )
        ):
            return Response(status_code=204)
        return HTMLResponse(BASE_HTML)

    return app, None
