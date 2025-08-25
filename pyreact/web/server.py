from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Optional, Set

from fastapi import FastAPI, Response, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from pyreact.core.hook import HookContext
from pyreact.core.runtime import schedule_rerender, run_renders, get_render_signal
from pyreact.web.nav_service import NavService
from pyreact.web.renderer import render_to_html
from pyreact.input.bus import InputBus
from pyreact.web.console import (
    ConsoleBuffer,
    enable_web_print,
    disable_web_print,
    _original_stdout,
)
from pyreact.web.ansi import ansi_to_html
from pyreact.web.broadcast import InMemoryBroadcast
from pyreact.web.ws_endpoint import register_ws_routes


# -------------------------
# Server state
# -------------------------
_WS_CLIENTS: Set[WebSocket] = set()
_pending_path: Optional[str] = None  # pending navigation until Router mounts
_ROOT_CTX: Optional[HookContext] = None  # global pointer for debug helpers

# Pub/Sub broadcast (in-memory)
broadcast = InMemoryBroadcast()

# Channel names
CHAN_HTML = "html"
CHAN_NAV = "nav"
CHAN_STDOUT = "stdout"
CHAN_MSG = "message"
CHAN_INPUT = "input"


_BASE_HTML = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Reaktiv App</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <style>
      html,body{margin:0;padding:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu}
      
      /* Chat message styles */
      .chat-message {
        display: flex;
        margin-bottom: 8px;
        animation: fadeIn 0.3s ease-in;
      }
      
      .chat-message.user {
        justify-content: flex-end;
      }
      
      .chat-message.system {
        justify-content: center;
      }
      
      .chat-message.assistant {
        justify-content: flex-start;
      }
      
      .message-bubble {
        max-width: 70%;
        padding: 12px 16px;
        border-radius: 18px;
        word-wrap: break-word;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
      }
      
      .chat-message.user .message-bubble {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-bottom-right-radius: 4px;
      }
      
      .chat-message.assistant .message-bubble {
        background: #f1f5f9;
        color: #1e293b;
        border: 1px solid #e2e8f0;
        border-bottom-left-radius: 4px;
      }
      
      .chat-message.system .message-bubble {
        background: #fef3c7;
        color: #92400e;
        border: 1px solid #fde68a;
        font-size: 0.9em;
        font-style: italic;
      }
      
      .message-bubble.info {
        background: #dbeafe !important;
        color: #1e40af !important;
        border-color: #93c5fd !important;
      }
      
      .message-bubble.warning {
        background: #fef3c7 !important;
        color: #92400e !important;
        border-color: #fde68a !important;
      }
      
      .message-bubble.error {
        background: #fee2e2 !important;
        color: #991b1b !important;
        border-color: #fca5a5 !important;
      }
      
      .message-sender {
        font-size: 0.75em;
        margin-bottom: 4px;
        opacity: 0.7;
        font-weight: 500;
      }
      
      .log-entry {
        background: #0b1020;
        color: #e7f0ff;
        padding: 8px 12px;
        border-radius: 6px;
        font-family: monospace;
        font-size: 14px;
        line-height: 1.4;
        margin-bottom: 4px;
        animation: fadeIn 0.3s ease-in;
        white-space: pre-wrap;
        word-wrap: break-word;
        overflow-x: auto;
      }
      
      @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }
    </style>
  </head>
  <body class="m-0 p-0 font-sans">
    <div id="dbg"
      class="fixed top-0 left-0 right-0 flex gap-2 items-center py-2 px-3 bg-[#f7f7f8] border-b border-[#e5e7eb] z-10">
      <button id="dbg-tree"
        class="px-3 py-1 border border-gray-300 rounded-[6px] bg-white cursor-pointer hover:bg-gray-100 transition-colors">
        Print VNode Tree (Ctrl+V)
      </button>
      <button id="dbg-trace"
        class="px-3 py-1 border border-gray-300 rounded-[6px] bg-white cursor-pointer hover:bg-gray-100 transition-colors">
        Print Render Trace (Ctrl+T)
      </button>
      <label
        class="inline-flex items-center gap-2 text-[14px]">
        <input type="checkbox" id="dbg-trace-enable" /> enable tracing
      </label>
    </div>
    <div id="chat-container" class="mt-[60px] mx-4 mb-4 max-h-[80vh] overflow-auto">
      <div id="chronological-output" class="space-y-2"></div>
    </div>
    <div id="root" class="p-4">{SSR}</div>
    <input id="cli"
      class="fixed bottom-0 left-0 right-0 py-2 px-4 border-0 border-t border-gray-300 text-[16px] outline-none"
      placeholder="type and press Enter…" autofocus />
    <script src="/static/app.js"></script>
  </body>
</html>"""


def create_fastapi_app(app_component_fn) -> tuple[FastAPI, HookContext]:
    """Create the FastAPI app with lifecycle via ``lifespan``.
    Returns ``(app, root_ctx)``.
    """
    app = FastAPI()
    # Serve static assets (JS/CSS) from the package's static directory
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    root_ctx = HookContext(app_component_fn.__name__, app_component_fn)
    global _ROOT_CTX
    _ROOT_CTX = root_ctx

    # ---------- local helpers (use root_ctx) ----------

    async def publish_html(html_now: str) -> None:
        payload = json.dumps({"channel": "ui", "type": "html", "html": html_now})
        await broadcast.publish(CHAN_HTML, payload)

    async def publish_nav(path: str) -> None:
        payload = json.dumps({"channel": "ui", "type": "nav", "path": path})
        await broadcast.publish(CHAN_NAV, payload)

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
                formatted_text = f"{color}[{sender.upper()}] {message_text}{reset}\n"
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
                    {"channel": "logs", "type": "stdout", "html": ansi_to_html(text)}
                )
        else:
            # Convert ANSI to HTML for colored output in browser
            payload = json.dumps(
                {"channel": "logs", "type": "stdout", "html": ansi_to_html(text)}
            )
        try:
            if text.startswith("__MESSAGE__:"):
                await broadcast.publish(CHAN_MSG, payload)
                return
        except Exception:
            pass
        await broadcast.publish(CHAN_STDOUT, payload)

    # --------- debug helpers ---------
    def print_vnode_tree() -> None:
        ctx = _ROOT_CTX
        if ctx is None:
            print("\x1b[90m[debug]\x1b[0m VNode tree not available yet.")
            return

        # Capture the VNode tree output as a single block
        import io
        import sys

        # Capture stdout temporarily
        old_stdout = sys.stdout
        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            print("\n\x1b[1m\x1b[36m=== VNode Tree ===\x1b[0m")
            ctx.render_tree()
            print("\x1b[1m\x1b[36m==================\x1b[0m\n")

            # Get the captured output as a single string
            vnode_output = captured_output.getvalue()

            # Send as a single log entry
            console = HookContext.get_service("console_buffer", ConsoleBuffer)
            console.append(vnode_output)

        finally:
            # Restore stdout
            sys.stdout = old_stdout

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

    async def _maybe_navigate(path: str) -> None:
        global _pending_path
        if path == "/favicon.ico":
            return

        navsvc = HookContext.get_service("nav_service", NavService)
        nav = navsvc.navigate
        if callable(nav):
            if navsvc.current != path:
                # Update RouterContext
                nav(path)
                # Schedule render; actual rendering happens in the render loop task
                schedule_rerender(root_ctx, reason=f"nav to {path}")
            _pending_path = None
        else:
            # Router has not mounted yet
            navsvc.current = path
            _pending_path = path
            schedule_rerender(root_ctx, reason=f"pending nav {path}")

    latest_html: Optional[str] = None
    html_updated_event: asyncio.Event = asyncio.Event()

    async def _render_loop() -> None:
        """Event-driven render loop: wait for render signal instead of constant polling."""
        nonlocal latest_html
        prev_html = None
        signal = get_render_signal()
        while True:
            # Wait until a render is scheduled or navigation is pending
            if not _pending_path and not signal.is_set():
                await signal.wait()

            if _pending_path:
                await _maybe_navigate(_pending_path)

            await run_renders()
            html_now = render_to_html(root_ctx)
            if html_now != prev_html:
                prev_html = html_now
                latest_html = html_now
                html_updated_event.set()
                await publish_html(html_now)

    # ---------- lifespan (startup/shutdown) ----------
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 1. Capture print() → ConsoleBuffer
        enable_web_print(echo_to_server_stdout=True)
        console = HookContext.get_service("console_buffer", ConsoleBuffer)

        # 2. Subscriber that pushes stdout to clients
        def _on_console(text: str):
            # we're inside the loop (startup), so scheduling is safe
            asyncio.create_task(_broadcast_stdout(text))

        console.subscribe(_on_console)

        # 3. Programmatic navigation (navigate(...) → browser pushState)
        navsvc = HookContext.get_service("nav_service", NavService)

        async def _nav_push(path: str):
            await publish_nav(path)

        def _nav_listener(path: str):
            asyncio.create_task(_nav_push(path))

        navsvc.subs.append(_nav_listener)

        # 4. First render will be scheduled now that there's an event loop
        schedule_rerender(root_ctx, reason="server startup")
        render_task = asyncio.create_task(_render_loop())
        input_task = asyncio.create_task(_input_consumer())

        # 5. Initial SSR will already use stdout accumulated so far
        app.state._cleanup = {
            "console": console,
            "console_listener": _on_console,
            "navsvc": navsvc,
            "nav_listener": _nav_listener,
            "render_task": render_task,
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
            try:
                navsvc.subs.remove(_nav_listener)
            except Exception:
                pass
            try:
                render_task.cancel()
            except Exception:
                pass
            try:
                input_task.cancel()
            except Exception:
                pass
            disable_web_print()

    # attach lifespan
    app.router.lifespan_context = lifespan  # Modern FastAPI allows setting it like this

    # ---------- routes ----------
    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204, media_type="image/x-icon")

    @app.get("/{full_path:path}")
    async def index(request: Request, full_path: str = ""):
        accept = request.headers.get("accept", "")
        if "text/html" not in accept.lower():
            # Avoid SSR for accidental assets (like the favicon)
            return Response(status_code=204)

        path = "/" + full_path
        # Schedule navigation and wait for the render loop to produce fresh HTML
        before = latest_html
        await _maybe_navigate(path)
        if latest_html is before:
            try:
                await asyncio.wait_for(html_updated_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        ssr_html = latest_html or render_to_html(root_ctx)
        html_updated_event.clear()

        # Don't include any stdout content in SSR - let WebSocket handle everything in chronological order
        stdout_ssr = ""

        return HTMLResponse(
            _BASE_HTML.replace("{SSR}", ssr_html).replace("{STDOUT}", stdout_ssr)
        )

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

    register_ws_routes(
        app,
        broadcast=broadcast,
        backlog_provider=_backlog_payloads,
        channels_to_forward=[CHAN_HTML, CHAN_NAV, CHAN_STDOUT, CHAN_MSG],
        input_channel=CHAN_INPUT,
        clients_set=_WS_CLIENTS,
    )

    async def _input_consumer():
        async with broadcast.subscribe(CHAN_INPUT) as subscriber:
            async for event in subscriber:
                raw = event.message
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("t")
                if t in ("hello", "nav"):
                    await _maybe_navigate(msg.get("path", "/"))
                elif t in ("text", "submit"):
                    bus = HookContext.get_service("input_bus", InputBus)
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
                        await broadcast.publish(CHAN_MSG, user_payload)

                    bus.emit(
                        {"type": t, "value": value, "source": "web", "ts": time.time()}
                    )
                elif t == "debug":
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

    return app, root_ctx
