import asyncio
from collections.abc import Callable
from typing import Optional
import inspect
from pyreact.core.core import hooks

def use_dspy_call(module) -> tuple[Callable[..., None], object, bool, Optional[Exception]]:
    """
    Policy: replace (the last call wins).
    Returns (run, result, loading, error). Forces an update after each success via 'ver'.
    """
    # ---------------- Reducer ----------------
    def reducer(state, action):
        typ = action["type"]
        if typ == "start":
            return {"status": "loading", "result": state["result"], "error": None, "ver": state["ver"]}
        if typ == "success":
            return {"status": "idle", "result": action["result"], "error": None, "ver": action["ver"]}
        if typ == "error":
            return {"status": "idle", "result": state["result"], "error": action["error"], "ver": action["ver"]}
        return state

    initial = {"status": "idle", "result": None, "error": None, "ver": 0}
    state, dispatch = hooks.use_reducer(reducer, initial)

    # -------------- Stable refs --------------
    alive_ref = hooks.use_memo(lambda: {"alive": True}, [])
    task_ref  = hooks.use_memo(lambda: {"task": None}, [])

    def _mount_cleanup():
        def _un(): alive_ref["alive"] = False
        return _un
    hooks.use_effect(_mount_cleanup, [])

    # -------------- Async worker --------------
    async def _do_call(inputs: dict):
        current_task = asyncio.current_task()

        try:
          ver = id(inspect.currentframe())
          result = await module.acall(**inputs), ver

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