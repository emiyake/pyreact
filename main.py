import asyncio

from core import component, hooks
from hook import HookContext
from input_dispatcher import _InputDispatcher
from provider import create_context
from runtime import run_renders, schedule_rerender
import time

# ainda em draft!!
UserContext = create_context(default="anonymous", name="User")

def use_user():
    """
    Devolve `(user, set_user)`; qualquer mudança re-renderiza consumidores.
    """
    user = hooks.use_context(UserContext)
    def _set(u):
        UserContext.set(u)
    return user, _set


@component
def Keystroke(on_key, name):
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

# @component
# def Child(label):
#     count, set_count = hooks.use_state(0)

#     # incrementa seu próprio estado toda vez que monta
#     hooks.use_effect(lambda: set_count(lambda v: v + 1), [])

#     hooks.use_effect(
#         lambda: print(f"{label}: count={count}"), [count]
#     )
#     return []

# @component
# def Parent():
#     return [
#         Child(key="A", label="ChildA"),
#         Child(key="B", label="ChildB")
#     ]


@component
def Child(name, on_count):

    count, set_count = hooks.use_state(0)

    user, set_user = use_user()

    def handle_key(char):
        if (char == name):
            next_val = count + 1
            set_count(next_val) 
            on_count(next_val)
        if (char == "C"):
            set_user(f"Novo user {name}")

    async def effect():
        print(f"👶 Child({name}) [user={user}] count={count}")

    def unmount():
        return lambda: print(f"❌ Cleanup: Child({name})")
    
    hooks.use_effect(effect, [user, count])
    hooks.use_effect(unmount, [])
    return [Keystroke(key=f"key-{name}", on_key=handle_key, name=name)]

@component
def Parent():
    show, set_show = hooks.use_state(True)

    def handle_count(count):
        if count > 2:
            set_show(False)

    if show:
        return [
            Child(name="A", on_count=handle_count),
            Child(name="B", on_count=handle_count),
        ]
    else:
        return [ Child(name="C", on_count=handle_count) ]

@component
def App():
    user, set_user = hooks.use_state("Anonymous")
    def iniciar():
        set_user("Edmar")
    hooks.use_effect(iniciar, [])

    return [UserContext(value=user, children=[Parent()])]




app_root = HookContext("App", App)

async def main():
    schedule_rerender(app_root)
    while True:
        await run_renders()
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(main())
