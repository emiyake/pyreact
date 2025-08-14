# dspy_integration.py ---------------------------------------
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple, Optional, Callable
import asyncio
import dspy  # use o backend que preferir (OpenAI/Azure/etc.)

from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context

# Ambiente a ser injetado na árvore
@dataclass
class DSPyEnv:
    lm: Any                      # ex.: dspy.OpenAI(model="gpt-4o-mini")
    optimizer: Optional[Any] = None  # ex.: dspy.teleprompt.MIPROv2(...)
    caches: Dict = field(default_factory=dict)  # (module_key -> module)
    compiled: Dict = field(default_factory=dict)  # (module_key -> compiled module)
    settings: Dict = field(default_factory=dict)  # livre p/ flags (timeout, etc.)

DSPyContext = create_context(default=None, name="DSPy")

def use_dspy_env() -> DSPyEnv:
    env = hooks.use_context(DSPyContext)
    if env is None:
        raise RuntimeError("DSPyProvider não foi montado acima na árvore.")
    return env

@component
def DSPyProvider(*, lm, optimizer=None, settings=None, children=None):
    # memoiza o ambiente para evitar recriações
    env = hooks.use_memo(
        lambda: DSPyEnv(lm=lm, optimizer=optimizer, settings=settings or {}),
        deps=[lm, optimizer, tuple(sorted((settings or {}).items()))]
    )
    # expõe o ambiente via Context
    return [DSPyContext(value=env, children=children or [])]

def _mk_module_key(module_cls, signature, name: Optional[str]) -> Tuple:
    sig_key = signature if isinstance(signature, str) else signature.__name__
    return (module_cls.__name__, sig_key, name)

def use_dspy_module(
    signature,                         # class dspy.Signature (ou string "q -> a")
    module_cls,                        # ex.: dspy.Predict / dspy.ChainOfThought
    *,
    name: str | None = None,           # opcional, p/ distinguir instâncias
    compile_with_optimizer: bool = False,
    deps: Optional[list] = None,       # quando mudar, recria (p. ex., muda few-shot)
) -> Any:
    """
    Cria (ou reutiliza do cache) um DSPy Module parametrizado pela Signature.
    Opcionalmente compila com o optimizer do Provider.
    """
    env = use_dspy_env()
    deps_key = tuple(deps) if deps is not None else None
    module_key = _mk_module_key(module_cls, signature, name)

    def factory():
        # 1) cria o módulo bruto
        mod = module_cls(signature)
        # 2) acopla LM do Provider (quando aplicável)
        # Muitos módulos do DSPy leem o LM global via dspy.settings; se preferir, force aqui:
        dspy.settings.configure(lm=env.lm)
        # 3) cacheia
        env.caches[module_key] = mod
        return mod

    # instancia ou pega do cache (memo local ao componente)
    mod = hooks.use_memo(
        lambda: env.caches.get(module_key) or factory(),
        deps=[module_key, deps_key]
    )

    # compilação (opcional) só uma vez por chave
    if compile_with_optimizer and env.optimizer is not None:
        compiled = env.compiled.get(module_key)

        if compiled is None:
            # dispara a compilação fora da thread do event-loop para não travar UI
            def kick_compile():
                def work():
                    # Exemplo genérico — ajuste à API do seu optimizer/teleprompter.
                    # Alguns pedem trainset/metric aqui.
                    return env.optimizer.compile(mod)
                fut = asyncio.get_running_loop().run_in_executor(None, work)

                async def _await_and_store():
                    try:
                        cm = await fut
                        env.compiled[module_key] = cm
                        # swap da referência "mod" no cache p/ compilado
                        env.caches[module_key] = cm
                    except Exception as e:
                        # (opcional) logar erro de compilação
                        pass
                asyncio.create_task(_await_and_store())

            hooks.use_effect(kick_compile, [module_key])  # roda uma vez

            # enquanto compila, devolvemos o "mod" cru; quando terminar, o cache troca

    return env.compiled.get(module_key, mod)