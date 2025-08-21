# dspy_integration.py ---------------------------------------
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple, Optional
import asyncio
import dspy

from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context


# Environment to be injected into the tree
@dataclass
class DSPyEnv:
    # Optional registry of models to enable per-component selection.
    # The "default" key is always required and serves as the default LM.
    models: Dict[str, Any] = field(default_factory=dict)
    optimizer: Optional[Any] = None
    caches: Dict = field(default_factory=dict)  # (module_key -> module)
    compiled: Dict = field(default_factory=dict)  # (module_key -> compiled module)
    settings: Dict = field(default_factory=dict)  # free for flags (timeout, etc.)


DSPyContext = create_context(default=None, name="DSPy")

# Optional global fallback when a provider is not mounted.
_FALLBACK_ENV: Optional["DSPyEnv"] = None


def use_dspy_env() -> DSPyEnv:
    env = hooks.use_context(DSPyContext)
    if env is None:
        # Lazily create a fallback environment to avoid hard crashes during SSR or early renders
        # when the provider has not yet mounted. This still allows per-call LM override via use_dspy_call.
        global _FALLBACK_ENV
        if _FALLBACK_ENV is None:
            try:
                default_lm = dspy.LM("openai/gpt-4o-mini")
            except Exception:
                default_lm = None
            _FALLBACK_ENV = DSPyEnv(
                models={"default": default_lm} if default_lm else {},
                optimizer=None,
            )
        return _FALLBACK_ENV
    return env


@component
def DSPyProvider(
    *,
    lm: Optional[Any] = None,
    models: Optional[Dict[str, Any]] = None,
    optimizer=None,
    settings=None,
    children=None,
):
    def _factory():
        model_registry = dict(models or {})
        default_lm = lm if lm is not None else model_registry.get("default")
        if default_lm is None:
            raise ValueError(
                "DSPyProvider requires either 'lm' or models['default'] to be provided."
            )

        # Ensure registry has a 'default'
        model_registry.setdefault("default", default_lm)
        return DSPyEnv(
            models=model_registry,
            optimizer=optimizer,
            settings=settings or {},
        )

    env = hooks.use_memo(
        _factory,
        deps=[
            lm,
            models and id(models),
            optimizer,
            tuple(sorted((settings or {}).items())),
        ],
    )
    # Configure the global default LM once per env
    dspy.configure(lm=env.models["default"])

    return [DSPyContext(value=env, children=children or [])]


def _mk_module_key(module_cls, signature, name: Optional[str]) -> Tuple:
    sig_key = signature if isinstance(signature, str) else signature.__name__
    return (module_cls.__name__, sig_key, name)


def use_dspy_module(
    signature,  # class dspy.Signature (or string "q -> a")
    module_cls,  # e.g.: dspy.Predict / dspy.ChainOfThought
    *,
    name: Optional[str] = None,  # optional, to distinguish instances
    compile_with_optimizer: bool = False,
    deps: Optional[list] = None,  # when changed, recreate (e.g., change few-shot)
) -> Any:
    """
    Creates (or reuses from cache) a DSPy Module parameterized by the Signature.
    Optionally compiles with the Provider's optimizer.
    """
    env = use_dspy_env()
    deps_key = tuple(deps) if deps is not None else None
    module_key = _mk_module_key(module_cls, signature, name)

    def factory():
        mod = module_cls(signature)
        env.caches[module_key] = mod
        return mod

    mod = hooks.use_memo(
        lambda: env.caches.get(module_key) or factory(), deps=[module_key, deps_key]
    )

    if compile_with_optimizer and env.optimizer is not None:
        compiled = env.compiled.get(module_key)

        if compiled is None:
            # trigger compilation outside the event-loop thread to avoid UI blocking
            def kick_compile():
                def work():
                    return env.optimizer.compile(mod)

                fut = asyncio.get_running_loop().run_in_executor(None, work)

                async def _await_and_store():
                    try:
                        cm = await fut
                        env.compiled[module_key] = cm
                        env.caches[module_key] = cm
                    except Exception:
                        pass

                asyncio.create_task(_await_and_store())

            hooks.use_effect(kick_compile, [module_key])  # run once

    return env.compiled.get(module_key, mod)
