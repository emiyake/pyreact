from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Optional, Set

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse

from pyreact.core.hook import HookContext
from pyreact.core.runtime import schedule_rerender, run_renders, get_render_signal
from pyreact.web.nav_service import NavService
from pyreact.web.renderer import render_to_html
from pyreact.input.bus import InputBus
from pyreact.web.console import ConsoleBuffer, enable_web_print, disable_web_print
from pyreact.web.ansi import ansi_to_html


# -------------------------
# Server state
# -------------------------
_WS_CLIENTS: Set[WebSocket] = set()
_pending_path: Optional[str] = None  # pending navigation until Router mounts
_ROOT_CTX: Optional[HookContext] = None  # global pointer for debug helpers


_BASE_HTML = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Reaktiv App</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      html,body{margin:0;padding:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu}
      #dbg{display:flex;gap:.5rem;align-items:center;padding:.4rem .8rem;background:#f7f7f8;border-bottom:1px solid #e5e7eb;z-index:10}
      #dbg button{padding:.35rem .6rem;border:1px solid #d1d5db;border-radius:6px;background:#fff;cursor:pointer}
      #dbg button:hover{background:#f3f4f6}
      #cli{position:fixed;bottom:0;left:0;right:0;padding:.6rem 1rem;border:0;border-top:1px solid #ddd;font-size:16px;outline:none}
      #root{padding:3rem 1rem 3.5rem}
      #stdout{line-height:1.5;white-space:pre-wrap;background:#0b1020;color:#e7f0ff;padding:12px;border-radius:10px;margin:0 1rem 1rem;max-height:90vh;overflow:auto}
    </style>
  </head>
  <body>
    <div id="dbg">
      <button id="dbg-tree">Print VNode Tree (Ctrl+T)</button>
    </div>
    <pre id="stdout">{STDOUT}</pre>
    <div id="root">{SSR}</div>
    <input id="cli" placeholder="type and press Enter…" autofocus />
    <script>
      (function(){
        const proto = (location.protocol === 'https:') ? 'wss://' : 'ws://';
        const ws = new WebSocket(proto + location.host + '/ws');
        const cli = document.getElementById('cli');
        const pre = document.getElementById('stdout');
        const dbgTreeBtn = document.getElementById('dbg-tree');

        ws.onopen = () => {
          ws.send(JSON.stringify({t:'hello', path: location.pathname}));
        };

        function scrollToBottom(el){ try{ el.scrollTop = el.scrollHeight; }catch{} }

        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'html') {
              document.getElementById('root').innerHTML = msg.html;
              return;
            }
            if (msg.type === 'nav') {
              if (location.pathname !== msg.path) history.pushState({}, '', msg.path);
              return;
            }
            if (msg.type === 'stdout') {
              pre.innerHTML += msg.html;
              scrollToBottom(pre);
              return;
            }
            } catch {
              // compatibility: legacy payload with raw HTML
              document.getElementById('root').innerHTML = ev.data;
            }
          };

          // Back/forward → server
        window.addEventListener('popstate', () => {
          try { ws.send(JSON.stringify({t:'nav', path: location.pathname})); } catch {}
        });

          // Input field (Keystroke → InputBus)
        cli.addEventListener('input', (e) => {
          try { ws.send(JSON.stringify({t:'text', v: e.target.value})); } catch {}
        });
        cli.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            try { ws.send(JSON.stringify({t:'submit', v: cli.value})); } catch {}
            cli.value = '';
            // keep focus and auto-scroll pre
            cli.focus();
            scrollToBottom(pre);
          }
        });

        // Debug: print VNode tree
        if (dbgTreeBtn) {
          dbgTreeBtn.addEventListener('click', () => {
            try { ws.send(JSON.stringify({t:'debug', what:'tree'})); } catch {}
          });
        }
        document.addEventListener('keydown', (e) => {
          const key = (e.key || '').toLowerCase();
          if ((e.ctrlKey || e.metaKey) && key === 't') {
            e.preventDefault();
            try { ws.send(JSON.stringify({t:'debug', what:'tree'})); } catch {}
          }
        });
      })();
    </script>
  </body>
</html>"""


def create_fastapi_app(app_component_fn) -> tuple[FastAPI, HookContext]:
    """Create the FastAPI app with lifecycle via ``lifespan``.
    Returns ``(app, root_ctx)``.
    """
    app = FastAPI()
    root_ctx = HookContext(app_component_fn.__name__, app_component_fn)
    global _ROOT_CTX
    _ROOT_CTX = root_ctx

    # ---------- local helpers (use root_ctx) ----------

    async def _broadcast_html(html_now: str) -> None:
        payload = json.dumps({"type": "html", "html": html_now})
        dead = []
        for ws in list(_WS_CLIENTS):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _WS_CLIENTS.discard(ws)

    async def _broadcast_nav(path: str) -> None:
        payload = json.dumps({"type": "nav", "path": path})
        dead = []
        for ws in list(_WS_CLIENTS):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _WS_CLIENTS.discard(ws)

    async def _broadcast_stdout(text: str) -> None:
        # Convert ANSI to HTML for colored output in browser
        payload = json.dumps({"type": "stdout", "html": ansi_to_html(text)})
        dead = []
        for ws in list(_WS_CLIENTS):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _WS_CLIENTS.discard(ws)

    # --------- debug helpers ---------
    def print_vnode_tree() -> None:
        ctx = _ROOT_CTX
        if ctx is None:
            print("\x1b[90m[debug]\x1b[0m VNode tree not available yet.")
            return
        print("\n\x1b[1m\x1b[36m=== VNode Tree ===\x1b[0m")
        ctx.render_tree()
        print("\x1b[1m\x1b[36m==================\x1b[0m\n")

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
                schedule_rerender(root_ctx)
            _pending_path = None
        else:
            # Router has not mounted yet
            navsvc.current = path
            _pending_path = path
            schedule_rerender(root_ctx)

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
                await _broadcast_html(html_now)

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
            await _broadcast_nav(path)

        def _nav_listener(path: str):
            asyncio.create_task(_nav_push(path))

        navsvc.subs.append(_nav_listener)

        # 4. First render will be scheduled now that there's an event loop
        schedule_rerender(root_ctx)
        render_task = asyncio.create_task(_render_loop())

        # 5. Initial SSR will already use stdout accumulated so far
        app.state._cleanup = {
            "console": console,
            "console_listener": _on_console,
            "navsvc": navsvc,
            "nav_listener": _nav_listener,
            "render_task": render_task,
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

        console = HookContext.get_service("console_buffer", ConsoleBuffer)
        stdout_ssr = ansi_to_html(console.dump())

        return HTMLResponse(
            _BASE_HTML.replace("{SSR}", ssr_html).replace("{STDOUT}", stdout_ssr)
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        _WS_CLIENTS.add(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                t = msg.get("t")
                if t in ("hello", "nav"):
                    await _maybe_navigate(msg.get("path", "/"))
                elif t in ("text", "submit"):
                    bus = HookContext.get_service("input_bus", InputBus)
                    bus.emit(
                        {
                            "type": t,
                            "value": msg.get("v", ""),
                            "source": "web",
                            "ts": time.time(),
                        }
                    )
                elif t == "debug" and msg.get("what") == "tree":
                    # Print VNode tree to stdout (will be streamed to browser)
                    print_vnode_tree()
        except WebSocketDisconnect:
            _WS_CLIENTS.discard(ws)
        except Exception:
            _WS_CLIENTS.discard(ws)

    return app, root_ctx
