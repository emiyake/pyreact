# dspy_integration.py ---------------------------------------
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple, Optional, Callable
import asyncio
import dspy  # use the backend you prefer (OpenAI/Azure/etc.)

from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context

# Environment to be injected into the tree
@dataclass
class DSPyEnv:
    lm: Any                      # e.g.: dspy.OpenAI(model="gpt-4o-mini")
    optimizer: Optional[Any] = None  # e.g.: dspy.teleprompt.MIPROv2(...)
    caches: Dict = field(default_factory=dict)  # (module_key -> module)
    compiled: Dict = field(default_factory=dict)  # (module_key -> compiled module)
    settings: Dict = field(default_factory=dict)  # free for flags (timeout, etc.)

DSPyContext = create_context(default=None, name="DSPy")

def use_dspy_env() -> DSPyEnv:
    env = hooks.use_context(DSPyContext)
    if env is None:
        raise RuntimeError("DSPyProvider was not mounted above in the tree.")
    return env

@component
def DSPyProvider(*, lm, optimizer=None, settings=None, children=None):
    # memoize the environment to avoid recreations
    env = hooks.use_memo(
        lambda: DSPyEnv(lm=lm, optimizer=optimizer, settings=settings or {}),
        deps=[lm, optimizer, tuple(sorted((settings or {}).items()))]
    )
    # expose the environment via Context
    return [DSPyContext(value=env, children=children or [])]

def _mk_module_key(module_cls, signature, name: Optional[str]) -> Tuple:
    sig_key = signature if isinstance(signature, str) else signature.__name__
    return (module_cls.__name__, sig_key, name)

def use_dspy_module(
    signature,                         # class dspy.Signature (or string "q -> a")
    module_cls,                        # e.g.: dspy.Predict / dspy.ChainOfThought
    *,
    name: str | None = None,           # optional, to distinguish instances
    compile_with_optimizer: bool = False,
    deps: Optional[list] = None,       # when changed, recreate (e.g., change few-shot)
) -> Any:
    """
    Creates (or reuses from cache) a DSPy Module parameterized by the Signature.
    Optionally compiles with the Provider's optimizer.
    """
    env = use_dspy_env()
    deps_key = tuple(deps) if deps is not None else None
    module_key = _mk_module_key(module_cls, signature, name)

    def factory():
        # 1) create the raw module
        mod = module_cls(signature)
        # 2) attach Provider's LM (when applicable)
        # Many DSPy modules read the global LM via dspy.settings; if preferred, force here:
        dspy.settings.configure(lm=env.lm)
        # 3) cache it
        env.caches[module_key] = mod
        return mod

    # instantiate or get from cache (local memo to the component)
    mod = hooks.use_memo(
        lambda: env.caches.get(module_key) or factory(),
        deps=[module_key, deps_key]
    )

    # compilation (optional) only once per key
    if compile_with_optimizer and env.optimizer is not None:
        compiled = env.compiled.get(module_key)

        if compiled is None:
            # trigger compilation outside the event-loop thread to avoid UI blocking
            def kick_compile():
                def work():
                    # Generic example — adjust to your optimizer/teleprompter API.
                    # Some require trainset/metric here.
                    return env.optimizer.compile(mod)
                fut = asyncio.get_running_loop().run_in_executor(None, work)

                async def _await_and_store():
                    try:
                        cm = await fut
                        env.compiled[module_key] = cm
                        # swap the "mod" reference in the cache for the compiled one
                        env.caches[module_key] = cm
                    except Exception as e:
                        # (optional) log compilation error
                        pass
                asyncio.create_task(_await_and_store())

            hooks.use_effect(kick_compile, [module_key])  # run once

            # while compiling, we return the raw "mod"; when finished, the cache swaps

    return env.compiled.get(module_key, mod)