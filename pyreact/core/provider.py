# provider.py ----------------------------------------------------
from contextvars import ContextVar
from functools import wraps
from weakref import WeakSet
from pyreact.core.core import component, hooks
from pyreact.core.runtime import schedule_rerender

# Global registry to prevent ContextVar instances from being garbage collected
_CONTEXT_REGISTRY = {}


def provider(ctx_var, *, prop="value"):
    def decorator(body_fn):
        @component
        @wraps(body_fn)
        def wrapper(*, key=None, **props):
            try:
                value = props.pop(prop)
            except KeyError:
                raise TypeError(f"Provider missing required prop '{prop}'")

            # Set the context value. It must persist between renders so that
            # child components re-rendered in isolation (via dispatch/set_state)
            # can still read it.  We only restore on unmount.
            prev = ctx_var.get()
            ctx_var.set(value)

            def unmount_cleanup():
                ctx_var.set(prev)

            # Empty deps [] = mount-only effect; cleanup runs only on unmount.
            hooks.use_effect(lambda: unmount_cleanup, [])

            return body_fn(**props)

        return wrapper

    return decorator


def create_context(*, default=None, name="Context", prop="value"):
    # Check if context already exists in registry
    context_key = f"{name}_{id(default) if default is not None else 'None'}"
    if context_key in _CONTEXT_REGISTRY:
        return _CONTEXT_REGISTRY[context_key]

    ctx_var = ContextVar(name, default=default)
    subs_set = WeakSet()

    @provider(ctx_var, prop=prop)
    def _Provider(**props):
        return props.get("children", [])

    class _Context:
        # used by use_context / unmount
        _ctx = ctx_var
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
                try:
                    schedule_rerender(hctx, reason=f"context {name} set")
                except Exception:
                    schedule_rerender(hctx)
            return token

        def __call__(self, **props):
            return _Provider(**props)

        def __repr__(self):
            return f"<Context {name!r} default={default!r}>"

    context_instance = _Context()

    # Register the context to prevent garbage collection
    _CONTEXT_REGISTRY[context_key] = context_instance

    return context_instance
