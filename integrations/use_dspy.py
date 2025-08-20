import asyncio
from collections.abc import Callable
from typing import Optional, Any
import inspect
import dspy
from pyreact.core.core import hooks
from integrations.dspy_integration import DSPyContext


def use_dspy_call(
    module, *, model: Optional[str] = None, lm: Optional[Any] = None
) -> tuple[Callable[..., None], object, bool, Optional[Exception]]:
    """
    Policy: replace (the last call wins).
    Returns (run, result, loading, error). Forces an update after each success via 'ver'.
    """

    # ---------------- Reducer ----------------
    def reducer(state, action):
        typ = action["type"]
        if typ == "start":
            return {
                "status": "loading",
                "result": state["result"],
                "error": None,
                "ver": state["ver"],
            }
        if typ == "success":
            return {
                "status": "idle",
                "result": action["result"],
                "error": None,
                "ver": action["ver"],
            }
        if typ == "error":
            return {
                "status": "idle",
                "result": state["result"],
                "error": action["error"],
                "ver": action["ver"],
            }
        return state

    initial = {"status": "idle", "result": None, "error": None, "ver": 0}
    state, dispatch = hooks.use_reducer(reducer, initial)

    # -------------- Stable refs --------------
    alive_ref = hooks.use_memo(lambda: {"alive": True}, [])
    task_ref = hooks.use_memo(lambda: {"task": None}, [])
    # Do not capture DSPy env during render; resolve at call-time to avoid early SSR errors

    def _mount_cleanup():
        def _un():
            alive_ref["alive"] = False

        return _un

    hooks.use_effect(_mount_cleanup, [])

    # -------------- Async worker --------------
    async def _do_call(inputs: dict):
        current_task = asyncio.current_task()

        try:
            ver = id(inspect.currentframe())
            # Determine LM to use for this call (resolve env at call-time)
            env = DSPyContext.get()
            if lm is not None:
                selected_lm = lm
            elif env is not None and model is not None:
                selected_lm = env.models.get(model, env.lm)
            elif env is not None:
                selected_lm = env.lm
            else:
                selected_lm = None  # fall back to dspy global default if configured

            # Run the module call under the chosen LM context
            async def _call():
                # If we have a selected LM, override via context for this call
                if selected_lm is not None:
                    ctx_mgr = dspy.context(lm=selected_lm)
                else:

                    class _Noop:
                        def __enter__(self):
                            return None

                        def __exit__(self, exc_type, exc, tb):
                            return False

                    ctx_mgr = _Noop()

                with ctx_mgr:
                    acall = getattr(module, "acall", None)
                    if acall is not None and inspect.iscoroutinefunction(acall):
                        return await acall(**inputs)
                    apredict = getattr(module, "apredict", None)
                    if apredict is not None and inspect.iscoroutinefunction(apredict):
                        return await apredict(**inputs)
                    predict = getattr(module, "predict", None)
                    if callable(predict):
                        return await asyncio.to_thread(predict, **inputs)
                    if callable(module):
                        return await asyncio.to_thread(module, **inputs)
                    raise AttributeError(
                        "DSPy module has no acall/apredict/predict/__call__ to invoke"
                    )

            result = await _call(), ver

            if alive_ref["alive"] and task_ref["task"] is current_task:
                dispatch({"type": "success", "result": result, "ver": ver})

        except Exception as e:
            if alive_ref["alive"] and task_ref["task"] is current_task:
                dispatch({"type": "error", "error": e, "ver": ver})

    # -------------- Exposed API --------------
    def _run(**inputs):
        dispatch({"type": "start"})
        t = asyncio.create_task(_do_call(inputs))
        task_ref["task"] = t

    run = hooks.use_callback(_run, deps=[id(module)])
    return run, state["result"], state["status"] == "loading", state["error"]
