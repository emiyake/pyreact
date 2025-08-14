from pyreact.core.core import component, hooks
from pyreact.input.bus import InputBus, Event

from pyreact.core.core import component, hooks
from pyreact.input.bus import InputBus, Event
from pyreact.input.focus import get_focus

import uuid

from pyreact.router.router import use_route

@component
def Keystroke(on_change=None, on_submit=None, *, path: str | None = None, exclusive: bool = False):
    state, set_state = hooks.use_state({"text": "", "submit_ver": 0})
    bus   = hooks.get_service("input_bus", InputBus)
    focus = get_focus()

    current_path, _ = use_route()
    active_by_route = (path is None) or (current_path == path)

    token = hooks.use_memo(lambda: uuid.uuid4().hex, [])

    # "refs" estáveis p/ callbacks (não entram nas deps dos efeitos)
    submit_fn = hooks.use_memo(lambda: on_submit, [on_submit])
    change_fn = hooks.use_memo(lambda: on_change, [on_change])

    # handler do bus: só depende de rota/foco
    def _handle(ev: Event):
        if not active_by_route:
            return
        if exclusive and not focus.is_current(token):
            return
        t = ev.get("type")
        v = ev.get("value", "") or ""
        if t == "text":
            set_state(lambda s: {"text": v, "submit_ver": s["submit_ver"]})
        elif t == "submit":
            set_state(lambda s: {"text": v, "submit_ver": s["submit_ver"] + 1})
        elif t == "key":
            pass
    handler = hooks.use_callback(_handle, deps=[active_by_route, exclusive])

    # subscribe no bus apenas quando ativo pela rota
    def _bus_effect():
        if not active_by_route:
            return
        maybe_unsub = bus.subscribe(handler)
        def _un():
            try:
                if callable(maybe_unsub):
                    maybe_unsub(); return
            except Exception:
                pass
            unsub = getattr(bus, "unsubscribe", None)
            if callable(unsub):
                try: unsub(handler)
                except Exception: pass
        return _un
    hooks.use_effect(_bus_effect, [handler, active_by_route])

    # foco exclusivo
    def _focus_effect():
        if exclusive and active_by_route:
            focus.acquire(token)
            def cleanup():
                focus.release(token)
            return cleanup
    hooks.use_effect(_focus_effect, [exclusive, active_by_route, token])

    # on_change: dispara só quando o texto muda
    def _on_change_effect():
        if change_fn is not None:
            change_fn(state["text"])
    hooks.use_effect(_on_change_effect, [state["text"]])

    # on_submit: dispara só após pelo menos um submit
    def _on_submit_effect():
        if submit_fn is not None and state["submit_ver"] > 0:
            submit_fn(state["text"])
    hooks.use_effect(_on_submit_effect, [state["submit_ver"]])

    return []