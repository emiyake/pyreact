import asyncio
from collections.abc import Callable
from typing import Optional

from pyreact.core.core import hooks

def use_dspy_call(module) -> tuple[Callable[..., None], object, bool, Optional[Exception]]:
    """
    Retorna (run, result, loading, error).

    - run(**inputs): dispara a inferência do módulo DSPy em background
    - result: último objeto retornado (ex.: .answer conforme a Signature)
    - loading/error: estados de UI
    """
    result, set_result   = hooks.use_state(None)
    loading, set_loading = hooks.use_state(False)
    error, set_error     = hooks.use_state(None)

    # --- Stable (non-reactive) "Refs" ---
    alive_ref   = hooks.use_memo(lambda: {"alive": True}, [])
    pending_ref = hooks.use_memo(lambda: {"token": 0}, [])  # increments with each run()

    # Unmount cleanup: marks as dead to ignore late deliveries
    def _mount_cleanup():
        def _unmount():
            alive_ref["alive"] = False
        return _unmount
    hooks.use_effect(_mount_cleanup, []) 

    async def _do_call(inputs: dict, token: int):
        try:
            def _invoke():
                return module(**inputs)
            out = await asyncio.get_running_loop().run_in_executor(None, _invoke)

            if alive_ref["alive"] and token == pending_ref["token"]:
                set_result(out)
                set_error(None)
        except Exception as e:
            if alive_ref["alive"] and token == pending_ref["token"]:
                set_error(e)
        finally:
            if alive_ref["alive"] and token == pending_ref["token"]:
                set_loading(False)

    # Stable function exposed to the caller
    def _run(**inputs):
        # invalidates previous calls
        pending_ref["token"] += 1
        token = pending_ref["token"]

        set_loading(True)
        set_error(None)
        asyncio.create_task(_do_call(inputs, token))

    run = hooks.use_callback(_run, deps=[id(module)])

    return run, result, loading, error