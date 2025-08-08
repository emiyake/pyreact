import asyncio

from core import component, hooks
from hook import HookContext
from input_dispatcher import _InputDispatcher
from provider import create_context
from router import Route, Router, use_route
from runtime import run_renders, schedule_rerender
import time

UserContext = create_context(default="anonymous", name="User")

def use_user():
    user = hooks.use_context(UserContext)
    def _set(u):
        UserContext.set(u)
    return user, _set


@component
def Keystroke(on_key):
    state, set_state = hooks.use_state({"key": "", "timestamp": 0})
    dispatcher = hooks.get_service("input_dispatcher", _InputDispatcher)

    def _listener(k):
        set_state({"key": k, "timestamp": time.time()})

    hooks.use_effect(lambda: on_key(state["key"]), [state])

    def unmount():
        dispatcher.unsubscribe(_listener)

    def sub():
        dispatcher.subscribe(_listener)
        return unmount

    hooks.use_effect(sub, [])  

    return []  


@component
def Text(text: str):
    hooks.use_effect(lambda: print(text), [])
    return []


@component
def Link(to, label):
    _, navigate = use_route()

    def handle(k):
        if k == label[0].lower():
            navigate(to)

    return [Keystroke(on_key=handle)]

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
                Route(key="r1", path="/",          children=[Home(key="home")]),
                Route(key="r2", path="/about",     children=[About(key="about")]),
                Link(key="l1", to="/",      label="home"),
                Link(key="l2", to="/about", label="about"),
            ],
        )
    ]

app_root = HookContext("App", App)

async def main():
    schedule_rerender(app_root)
    while True:
        await run_renders()
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(main())
