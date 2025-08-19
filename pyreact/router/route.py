# route.py
import re
from typing import Dict, Any, Optional
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from pyreact.web.nav_service import NavService
from .router import use_route  # function that reads RouterContext

# Optional context to pass captured params
RouteParamsContext = create_context(default={}, name="RouteParams")

# Removed global render-cycle state. Router will pick the Route VNode to render.

def use_route_params():
    return hooks.use_context(RouteParamsContext)

def use_query_params() -> Dict[str, str]:
    """Hook to get query parameters from current URL"""
    navsvc = hooks.get_service("nav_service", NavService)
    return navsvc.get_query_params()

def use_navigate():
    """Hook that returns an enhanced navigate function with parameter support"""
    _, navigate = use_route()
    return navigate

@component
def Route(path: str, *, children, exact: bool = True):
    """Routing rules:
      - If ``path`` starts with ``'^'`` → interpreted as REGEX (``re.match``).
      - If ``path`` ends with ``'/*'`` → prefix (e.g., ``'/users/*'`` matches ``'/users'`` and ``'/users/42'``).
      - ``':param'`` becomes a named group (e.g., ``'/users/:id'`` → ``params['id']``).
      - ``'*'`` at the end becomes ``splat`` (e.g., ``'/files/*'`` → ``params['splat']``).
      - When matched, inject params into :class:`RouteParamsContext`.
    """
    current_full, _ = use_route()  # e.g. '/about?search=test'
    # Use only the path portion for route matching (without query params)
    current = current_full.split('?')[0]
    
    # Router now selects the single matching Route VNode; this component
    # only needs to inject params and render children when matched.

    def build_matcher():
        # 1. Explicit regex
        if path.startswith("^"):
            rx = re.compile(path)
            def match(s: str):
                m = rx.match(s)
                return (m is not None, m.groupdict() if m else {})
            return match

        # 2. ':param' and '*' → regex
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
                elif c == "*" and i == len(pat) - 1:  # splat at end
                    tokens.append("(?P<splat>.*)")
                    i += 1
                else:
                    tokens.append(re.escape(c))
                    i += 1
            body = "".join(tokens)
            anchor = "^" + body + ("$" if exact_local else "")
            return re.compile(anchor)

        # 3. Prefix with '/*'
        if path.endswith("/*"):
            rx = to_regex(path[:-1], exact_local=False)  # leave open
            def match(s: str):
                m = rx.match(s)
                return (m is not None, m.groupdict() if m else {})
            return match

        # 4. Exact (or exact with params)
        rx = to_regex(path, exact_local=exact)
        def match(s: str):
            m = rx.match(s)
            return (m is not None, m.groupdict() if m else {})
        return match

    matcher = hooks.use_memo(build_matcher, [path, exact])

    ok, params = matcher(current)
    
    # If this route doesn't match, return empty
    if not ok:
        return []
    
    # pass params to children (if any)
    return [RouteParamsContext(value=params, children=children)]

