# route.py
import re
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from .router import use_route  # sua função que lê RouterContext

# Contexto opcional para repassar params capturados
RouteParamsContext = create_context(default={}, name="RouteParams")

def use_route_params():
    return hooks.use_context(RouteParamsContext)

@component
def Route(path: str, *, children, exact: bool = True):
    """
    Regras:
      - Se path começa com '^' → interpretado como REGEX (re.match).
      - Se path termina com '/*' → prefixo (ex.: '/users/*' casa '/users' e '/users/42').
      - ':param' vira grupo nomeado (ex.: '/users/:id' → params['id']).
      - '*' no fim vira 'splat' (ex.: '/files/*' → params['splat']).
      - Caso casado, injeta params em RouteParamsContext.
    """
    current, _ = use_route()  # p.ex. '/about'

    def build_matcher():
        # 1) Regex explícito
        if path.startswith("^"):
            rx = re.compile(path)
            def match(s: str):
                m = rx.match(s)
                return (m is not None, m.groupdict() if m else {})
            return match

        # 2) ':param' e '*' → regex
        def to_regex(pat: str, exact_local: bool):
            tokens = []
            i = 0
            while i < len(pat):
                c = pat[i]
                if c == ":":
                    j = i + 1
                    while j < len(pat) and (pat[j].isalnum() or pat[j] == "_"):
                        j += 1
                    name = pat[i+1:j] or "param"
                    tokens.append(f"(?P<{name}>[^/]+)")
                    i = j
                elif c == "*" and i == len(pat) - 1:  # splat no final
                    tokens.append("(?P<splat>.*)")
                    i += 1
                else:
                    tokens.append(re.escape(c))
                    i += 1
            body = "".join(tokens)
            anchor = "^" + body + ("$" if exact_local else "")
            return re.compile(anchor)

        # 3) Prefixo com '/*'
        if path.endswith("/*"):
            rx = to_regex(path[:-1], exact_local=False)  # deixa aberto
            def match(s: str):
                m = rx.match(s)
                return (m is not None, m.groupdict() if m else {})
            return match

        # 4) Exato (ou exato com params)
        rx = to_regex(path, exact_local=exact)
        def match(s: str):
            m = rx.match(s)
            return (m is not None, m.groupdict() if m else {})
        return match

    matcher = hooks.use_memo(build_matcher, [path, exact])

    ok, params = matcher(current)
    if not ok:
        return []

    # passa params para os filhos (se houver)
    return [RouteParamsContext(value=params, children=children)]