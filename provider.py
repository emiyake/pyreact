

from contextvars import ContextVar
from functools import wraps
from weakref import WeakSet
from core import component, hooks
from runtime import schedule_rerender


def provider(ctx_var, *, prop="value"):
    def deco(fn):
        @component
        @wraps(fn)
        def wrapper(*, key=None, **props):
            try:
                value = props.pop(prop)
            except KeyError:
                raise TypeError(f"Provider missing required prop '{prop}'")

            def effect():
                token = ctx_var.set(value)
                return lambda: ctx_var.reset(token)

            hooks.use_effect(effect, [value])
            return fn(**props)

        return wrapper

    return deco

def create_context(*, default=None, name="Context", prop="value"):
    ctx_var   = ContextVar(name, default=default)
    subs_set  = WeakSet()

    @provider(ctx_var, prop=prop)
    def _Provider(**props):
        return props.get("children", [])

    class _Context:

        # usados por use_context / unmount
        _ctx  = ctx_var
        _subs = subs_set

        @staticmethod
        def _subscribe(hctx):
            subs_set.add(hctx)

        @staticmethod
        def get():
            return ctx_var.get()

        @staticmethod
        def reset(token):
            return ctx_var.reset(token)

        @staticmethod
        def set(value):
            token = ctx_var.set(value)
            for hctx in list(subs_set):
                schedule_rerender(hctx)
            return token

        def __call__(self, **props):
            return _Provider(**props)

        def __repr__(self):
            return f"<Context {name!r} default={default!r}>"

    return _Context()
