from pyreact.boot import run_terminal, run_web
from pyreact.components.keystroke import Keystroke
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from pyreact.router import Route, Router, use_route

UserContext = create_context(default="anonymous", name="User")

def use_user():
    user = hooks.use_context(UserContext)
    def _set(u):
        UserContext.set(u)
    return user, _set


@component
def Text(text: str):
    hooks.use_effect(lambda: print(text), [])
    return []


@component
def Link(to, label):
    _, navigate = use_route()

    def handle(k):
        if k == label[0].lower():
            print("To: ", to)
            navigate(to)

    return [Keystroke(on_submit=handle)]

@component
def Home():
    return [Text(key="h", text="🏠 Home")]

@component
def About():
    return [Text(key="a", text="ℹ️  About")]

@component
def NotFound():
    return [Text(key="404", text="404 – not found")]

@component
def App():
    return [
        Router(
            initial="/",
            children=[
                Route(key="r1", path="/home",          children=[Home(key="home")]),
                Route(key="r2", path="/about",     children=[About(key="about")]),
                Link(key="l1", to="/home",      label="home"),
                Link(key="l2", to="/about", label="about"),
            ],
        )
    ]

if __name__ == "__main__":
    # run_terminal(App, prompt="> ", fps=20)
    run_web(App, host="127.0.0.1", port=8000)
