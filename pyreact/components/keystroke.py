from pyreact.core.core import component, hooks
from pyreact.input.bus import InputBus, Event


@component
def Keystroke(on_change=None, on_submit=None):
    state, set_state = hooks.use_state({"text": "", "submit_ver": 0})
    bus = hooks.get_service("input_bus", InputBus)

    # Stable "refs" for callbacks (not part of effect deps)
    submit_fn = hooks.use_memo(lambda: on_submit, [on_submit])
    change_fn = hooks.use_memo(lambda: on_change, [on_change])

    # Bus handler
    def _handle(ev: Event):
        t = ev.get("type")
        v = ev.get("value", "") or ""
        if t == "text":
            set_state(lambda s: {"text": v, "submit_ver": s["submit_ver"]})
        elif t == "submit":
            set_state(lambda s: {"text": v, "submit_ver": s["submit_ver"] + 1})
        elif t == "key":
            pass

    handler = hooks.use_callback(_handle, deps=[])

    # Subscribe to the bus
    def _bus_effect():
        maybe_unsub = bus.subscribe(handler)

        def _un():
            try:
                if callable(maybe_unsub):
                    maybe_unsub()
                    return
            except Exception:
                pass
            unsub = getattr(bus, "unsubscribe", None)
            if callable(unsub):
                try:
                    unsub(handler)
                except Exception:
                    pass

        return _un

    hooks.use_effect(_bus_effect, [handler])

    # on_change: triggers only when the text changes
    def _on_change_effect():
        if change_fn is not None:
            change_fn(state["text"])

    hooks.use_effect(_on_change_effect, [state["text"]])

    # on_submit: triggers only after at least one submit
    def _on_submit_effect():
        if submit_fn is not None and state["submit_ver"] > 0:
            submit_fn(state["text"])

    hooks.use_effect(_on_submit_effect, [state["submit_ver"]])

    return []
