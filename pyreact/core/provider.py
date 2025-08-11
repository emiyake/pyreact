

from contextvars import ContextVar
from functools import wraps
from weakref import WeakSet
from pyreact.core.core import component, hooks
from pyreact.core.runtime import schedule_rerender

def provider(ctx_var, *, prop="value"):
    def decorator(body_fn):
        @component
        @wraps(body_fn)
        def wrapper(*, key=None, **props):

            try:
                value = props.pop(prop)
            except KeyError:
                raise TypeError(f"Provider missing required prop '{prop}'")

            token = ctx_var.set(value)

            def cleanup():
                ctx_var.reset(token)

            # registra o cleanup – roda no próximo commit ou un-mount
            hooks.use_effect(lambda: cleanup, [value])

            return body_fn(**props)

        return wrapper
    return decorator

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
