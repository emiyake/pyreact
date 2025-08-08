

from provider import create_context
from core import component, hooks
import re


RouterContext = create_context(default={"/": None}, name="Router")


def use_route():
    state = hooks.use_context(RouterContext)   # formato do state: {'/home': navigate}
    (path, navigate), = state.items()

    return path, navigate

@component
def Router(*, initial="/", children):

    def make_state(p):
        def navigate(new_path):
            RouterContext.set({new_path: navigate})
        return {p: navigate}

    state = hooks.use_memo(lambda: make_state(initial), [initial])
    return [RouterContext(value=state, children=children)]


@component
def Route(path, *, children):
    current, _ = use_route()
    match = re.match(path, current) if path.startswith("^") else path == current
    return children if match else []