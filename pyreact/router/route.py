# route.py
from typing import Dict, List, Optional, Any
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from pyreact.web.nav_service import NavService
from .router import use_route  # function that reads RouterContext
from .router import use_routes_catalog  # expose catalog to RouteAgent
from .match import compile_route_pattern

RouteParamsContext = create_context(default={}, name="RouteParams")


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
def RouteAgent(message: str, *, children=None):
    """
    A lightweight routing agent that maps a free-text user message to a route.

    - Reads the available routes from RoutesCatalogContext
    - Uses simple keyword/heuristic matching to choose a target path
    - Calls navigate to perform the routing
    - If no match is found, renders children (fallback)

    This is intentionally pluggable: you can replace the scoring function with
    an LLM call to rank candidate routes based on descriptions/utterances.
    """
    current_full, navigate = use_route()
    catalog = use_routes_catalog() or []

    # Guard: empty message → do nothing
    if not isinstance(message, str) or not message.strip():
        return children or []

    # Scoring: look for explicit name or path keywords in message (case-insensitive)
    msg = message.lower()

    def score(entry: Dict[str, str]) -> int:
        s = 0
        name = str(entry.get("name", "")).lower()
        path = str(entry.get("path", "")).lower()
        # Basic heuristics
        if name and name in msg:
            s += 5
        if path and path.strip("/") and path.strip("/") in msg:
            s += 4
        # Match common tokens
        tokens = [t for t in ["home", "about", "help", "qa", "search"] if t in msg]
        for t in tokens:
            if t in name or t in path:
                s += 2
        return s

    ranked = sorted(catalog, key=score, reverse=True)
    best = ranked[0] if ranked else None

    def _effect_navigate():
        if not best:
            return
        # Don't navigate if already on a matching path base
        target = best.get("path", "/")
        if current_full.split("?")[0] != target:
            navigate(target)

    hooks.use_effect(_effect_navigate, [message, current_full, str(catalog)])

    # Render nothing; or fallback children while deciding
    return children or []


@component
def Route(
    path: str,
    *,
    children,
    exact: bool = True,
    name: Optional[str] = None,
    description: Optional[str] = None,
    utterances: Optional[List[str]] = None,
    default_params: Optional[Dict[str, Any]] = None,
):
    """Routing rules:
    - If ``path`` starts with ``'^'`` → interpreted as REGEX (``re.match``).
    - If ``path`` ends with ``'/*'`` → prefix (e.g., ``'/users/*'`` matches ``'/users'`` and ``'/users/42'``).
    - ``':param'`` becomes a named group (e.g., ``'/users/:id'`` → ``params['id']``).
    - ``'*'`` at the end becomes ``splat`` (e.g., ``'/files/*'`` → ``params['splat']``).
    - When matched, inject params into :class:`RouteParamsContext`.

    Optional metadata props used by the Router's route catalog:
    - ``name``: friendly name (e.g., "home", "about").
    - ``description``: description for ranking/help.
    - ``utterances``: common expressions to map free text.
    - ``default_params``: default parameters if the route is parameterized.
    """
    current_full, _ = use_route()  # e.g. '/about?search=test'
    # Use only the path portion for route matching (without query params)
    current = current_full.split("?")[0]

    # Router now selects the single matching Route VNode; this component
    # only needs to inject params and render children when matched.

    def build_matcher():
        rx = compile_route_pattern(path, exact)

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
