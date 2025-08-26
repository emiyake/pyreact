from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager


from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

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

        # 1. Create application state and runner
        server_loop = asyncio.get_running_loop()
        runner = app_runner or bootstrap(app_component_fn)
        state = ServerState()

        # 2. Capture print() → ConsoleBuffer
        enable_web_print(echo_to_server_stdout=True)
        console = HookContext.get_service("console_buffer", ConsoleBuffer)

        # 3. Subscriber that pushes stdout to clients
        def _on_console(text: str):
            # we're inside the loop (startup), so scheduling is safe
            asyncio.create_task(_broadcast_stdout(text))

        console.subscribe(_on_console)

        # 4. Bridge HTML/nav updates from the runner
        async def publish_html(html_now: str) -> None:
            payload = json.dumps({"channel": "ui", "type": "html", "html": html_now})
            await state.broadcast.publish(CHAN_HTML, payload)

        async def publish_nav(path: str) -> None:
            payload = json.dumps({"channel": "ui", "type": "nav", "path": path})
            await state.broadcast.publish(CHAN_NAV, payload)

        try:
            runner.attach_web_bridge(
                on_html=publish_html,
                on_nav=publish_nav,
                target_loop=server_loop,
            )
        except Exception:
            pass

        # 5. Define helper to broadcast stdout (ConsoleBuffer subscriber below)

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

        # --------- debug helpers (use runner) ---------
        def print_vnode_tree() -> None:
            try:
                runner.print_vnode_tree()
            except Exception:
                print("\x1b[90m[debug]\x1b[0m VNode tree not available.")

        def print_render_trace() -> None:
            try:
                runner.print_render_trace()
            except Exception:
                print("\x1b[90m[debug]\x1b[0m render trace not available.")

        async def _maybe_navigate(path: str) -> None:
            if path == "/favicon.ico":
                return
            try:
                runner.nav(path)
            except Exception:
                pass

        # No server-side RenderLoop; runner publishes HTML via bridge

        async def _handle_input_message(msg: dict) -> None:
            t = msg.get("t")

            if t in ("hello", "nav"):
                await _maybe_navigate(msg.get("path", "/"))
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

                    try:
                        ret = runner.invoke(value, wait=True)
                        if ret:
                            try:
                                # Print the returned lines to the console buffer so they appear in the UI
                                console.append(
                                    ret + ("\n" if not ret.endswith("\n") else "")
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass
                    return

            if t == "debug":
                what = msg.get("what")
                if what == "tree":
                    print_vnode_tree()
                elif what == "trace":
                    print_render_trace()
                elif what == "enable_trace":
                    try:
                        from pyreact.core.debug import enable_tracing, clear_traces

                        clear_traces()
                        enable_tracing()
                        print("\x1b[90m[debug]\x1b[0m tracing enabled.")
                    except Exception:
                        print("\x1b[90m[debug]\x1b[0m could not enable tracing.")
                elif what == "disable_trace":
                    try:
                        from pyreact.core.debug import disable_tracing

                        disable_tracing()
                        print("\x1b[90m[debug]\x1b[0m tracing disabled.")
                    except Exception:
                        print("\x1b[90m[debug]\x1b[0m could not disable tracing.")

        input_consumer = InputConsumer(
            broadcast=state.broadcast,
            input_channel=CHAN_INPUT,
            handle_message=_handle_input_message,
        )

        # 6. Start input consumer (runner loop handles rendering & publishing)
        input_task = asyncio.create_task(input_consumer.run())

        # 7. Define backlog provider function
        def _backlog_payloads() -> list[str]:
            payloads: list[str] = []
            try:
                console = HookContext.get_service("console_buffer", ConsoleBuffer)
                stdout_lines = console.dump().split("\n")
                for line in stdout_lines:
                    if line.startswith("__MESSAGE__:"):
                        try:
                            message_json = line[12:]
                            message_data = json.loads(message_json)
                            payloads.append(
                                json.dumps(
                                    {
                                        "channel": "chat",
                                        "type": "message",
                                        "data": message_data,
                                    }
                                )
                            )
                        except json.JSONDecodeError:
                            pass
                    elif line.strip():
                        payloads.append(
                            json.dumps(
                                {
                                    "channel": "logs",
                                    "type": "stdout",
                                    "html": ansi_to_html(line + "\n"),
                                }
                            )
                        )
            except Exception:
                pass
            return payloads

        # 8. Store state in app for access by routes
        app.state.server_state = state
        app.state.app_runner = runner

        # 9. Register WebSocket routes
        register_ws_routes(
            app,
            broadcast=state.broadcast,
            backlog_provider=_backlog_payloads,
            channels_to_forward=[CHAN_HTML, CHAN_NAV, CHAN_STDOUT, CHAN_MSG],
            input_channel=CHAN_INPUT,
            clients_set=state.clients,
        )

        # 9. Initial SSR will already use stdout accumulated so far
        app.state._cleanup = {
            "console": console,
            "console_listener": _on_console,
            "render_task": None,
            "input_task": input_task,
        }

        try:
            yield
        finally:
            # shutdown
            try:
                console.unsubscribe(_on_console)
            except Exception:
                pass
            # nav listener belongs to the runner; runner.shutdown() will clean it
            try:
                if app.state._cleanup.get("render_task") is not None:
                    app.state._cleanup.get("render_task").cancel()
            except Exception:
                pass
            try:
                runner.shutdown()
            except Exception:
                pass
            try:
                input_task.cancel()
            except Exception:
                pass
            disable_web_print()

            # Clean up global state
            try:
                HookContext._services.clear()
            except Exception:
                pass

    app.router.lifespan_context = lifespan

    def print_render_trace() -> None:
        try:
            from pyreact.core.debug import print_last_trace

            # Capture the render trace output as a single block
            import io
            import sys

            # Capture stdout temporarily
            old_stdout = sys.stdout
            captured_output = io.StringIO()
            sys.stdout = captured_output

            try:
                print_last_trace()

                # Get the captured output as a single string
                trace_output = captured_output.getvalue()

                # Send as a single log entry
                console = HookContext.get_service("console_buffer", ConsoleBuffer)
                console.append(trace_output)

            finally:
                # Restore stdout
                sys.stdout = old_stdout

        except Exception:
            print("\x1b[90m[debug]\x1b[0m render trace not available.")

    # ---------- routes ----------
    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204, media_type="image/x-icon")

    @app.get("/{full_path:path}")
    async def index(request: Request, full_path: str = ""):
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
            # Avoid SSR for accidental assets (like the favicon)
            return Response(status_code=204)

        path = "/" + full_path

        # Get state and runner from app.state (set by lifespan)
        state = getattr(request.app.state, "server_state", None)
        runner = getattr(request.app.state, "app_runner", None)

        if runner is None or state is None:
            # Server not fully started yet
            return HTMLResponse(
                BASE_HTML.replace("{SSR}", "<div>Loading...</div>").replace(
                    "{STDOUT}", ""
                )
            )

        # Navigate and render via runner
        try:
            runner.nav(path)
        except Exception:
            pass

        ssr_html = ""
        try:
            ssr_html = runner.render_html()
        except Exception:
            ssr_html = ""

        # Don't include any stdout content in SSR - let WebSocket handle everything in chronological order
        stdout_ssr = ""

        return HTMLResponse(
            BASE_HTML.replace("{SSR}", ssr_html).replace("{STDOUT}", stdout_ssr)
        )

    @app.post("/_test/input")
    async def test_input(request: Request):
        try:
            data = await request.json()
        except Exception:
            # fallback to query params
            qp = dict(request.query_params)
            data = {"t": qp.get("t"), "v": qp.get("v", "")}
        t = data.get("t") or data.get("type")
        v = data.get("v") or data.get("value", "")
        if not t:
            return Response(status_code=400, content="missing 't'")
        payload = json.dumps({"t": t, "v": v})
        state = getattr(request.app.state, "server_state", None)
        if state is None:
            return Response(status_code=503, content="server not ready")
        await state.broadcast.publish(CHAN_INPUT, payload)
        return Response(status_code=204)

    return app, None
