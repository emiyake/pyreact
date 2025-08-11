from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context

RouterContext = create_context(default={"/": None}, name="Router")

def use_route():
    state = hooks.use_context(RouterContext)     # {'/home': navigate}
    (path, navigate), = state.items()
    return path, navigate

@component
def Router(*, initial="/", children):
    """
    Sincroniza rota com um serviço global para o servidor enviar/receber navegações.
    """
    navsvc = hooks.get_service("nav_service", lambda: {"subs": [], "navigate": None, "current": "/"})

    def make_state(p, svc):
        def navigate(new_path: str):
            RouterContext.set({new_path: navigate})
            svc["current"] = new_path
            # notifica assinantes (servidor irá empurrar 'nav' para o browser)
            for fn in list(svc["subs"]):
                try: fn(new_path)
                except Exception: pass
        svc["current"] = p
        svc["navigate"] = navigate
        return {p: navigate}

    state = hooks.use_memo(lambda: make_state(initial, navsvc), [initial])
    return [RouterContext(value=state, children=children)]