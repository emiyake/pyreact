from pyreact.core.core import component, hooks
from pyreact.input.bus import InputBus, Event

@component
def Keystroke(on_change=None, on_submit=None):
    """Collect text from the :class:`InputBus`.

    - ``on_change(text)``: called on every text change
    - ``on_submit(text)``: called when the user confirms (Enter)
    """
    state, set_state = hooks.use_state({"text": "", "submit_ver": 0})
    bus = hooks.get_service("input_bus", InputBus)

    def _on_event(ev: Event):
        t = ev.get("type")
        v = ev.get("value", "") or ""
        if t == "text":
            set_state(lambda s: {"text": v, "submit_ver": s["submit_ver"]})
        elif t == "submit":
            set_state(lambda s: {"text": v, "submit_ver": s["submit_ver"] + 1})
        elif t == "key":
            # handle character-by-character here if desired (optional)
            pass

    def _mount():
        bus.subscribe(_on_event)
        def _un():
            bus.unsubscribe(_on_event)
        return _un
    hooks.use_effect(_mount, [])

    if on_change is not None:
        hooks.use_effect(lambda: on_change(state["text"]), [state["text"]])

    if on_submit is not None:
        hooks.use_effect(lambda: on_submit(state["text"]), [state["submit_ver"]])

    return []
