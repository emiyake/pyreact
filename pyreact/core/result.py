from .core import component, hooks


@component
def Result(value=None):
    """Declarative component that signals a forward() result.

    Usage::

        return [
            Message(key="msg", text=answer, ...),
            Result(key="result", value=answer),
        ]

    - ``value=None`` signals that the component is still working (pending).
    - Any non-None value resolves the active forward request.
    - When no forward is active, this is a no-op.
    """
    hooks.use_result(value)
    return []
