from typing import Dict, Any, Optional, Union
from urllib.parse import urlencode, urlparse, urlunparse
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from pyreact.web.nav_service import NavService

RouterContext = create_context(default={"/": None}, name="Router")


def _build_url(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
    fragment: str = "",
) -> str:
    """Build a URL with path parameters and query string"""
    # Replace path parameters like :id with actual values
    if params:
        for key, value in params.items():
            path = path.replace(f":{key}", str(value))

    # Build query string
    query_string = ""
    if query:
        # Filter out None values and convert everything to strings
        clean_query = {k: str(v) for k, v in query.items() if v is not None}
        if clean_query:
            query_string = urlencode(clean_query)

    # Combine everything
    parsed = urlparse(path)
    result = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            query_string,
            fragment,
        )
    )

    return result


def use_route():
    state = hooks.use_context(RouterContext)  # e.g. {'/home': navigate}
    ((path, navigate),) = state.items()
    return path, navigate


@component
def Router(*, initial=None, children):
    navsvc = hooks.get_service("nav_service", NavService)
    if initial is None:
        initial = navsvc.current

    # Force Router to rerender on nav changes so selected Route updates
    _version, _set_version = hooks.use_state(0)

    def _on_nav(_):
        _set_version(lambda v: v + 1)

    _nav_handler = hooks.use_memo(lambda: _on_nav, [])

    def _effect_subscribe():
        navsvc.subs.append(_nav_handler)

        def _cleanup():
            try:
                navsvc.subs.remove(_nav_handler)
            except ValueError:
                pass

        return _cleanup

    hooks.use_effect(_effect_subscribe, [_nav_handler, navsvc])

    def make_state(p, svc):
        def navigate(
            new_path: Union[str, Dict[str, Any]],
            params: Optional[Dict[str, Any]] = None,
            query: Optional[Dict[str, Any]] = None,
            fragment: str = "",
        ):
            """
            Navigate to a new path with optional parameters and query string.

            Args:
                new_path: Either a string path or a dict with 'path', 'params', 'query', 'fragment'
                params: Path parameters to replace in the URL (e.g., {'id': 123})
                query: Query string parameters (e.g., {'search': 'hello', 'page': 1})
                fragment: URL fragment/hash
            """
            if isinstance(new_path, dict):
                # If new_path is a dict, extract components
                path_str = new_path.get("path", "/")
                params = new_path.get("params", params)
                query = new_path.get("query", query)
                fragment = new_path.get("fragment", fragment)
            else:
                path_str = new_path

            # Build the final URL with parameters and query string
            final_url = _build_url(path_str, params, query, fragment)

            RouterContext.set({final_url: navigate})
            svc.current = final_url
            for fn in list(svc.subs):
                try:
                    fn(final_url)
                except Exception:
                    pass

        svc.current = p
        svc.navigate = navigate
        return {p: navigate}

    state = hooks.use_memo(lambda: make_state(initial, navsvc), [initial])

    # Build the current router value using the CURRENT path and the stable navigate fn
    current_path_only = navsvc.current
    navigate_fn = getattr(navsvc, "navigate", None)
    if navigate_fn is None:
        # fallback to the navigate created in make_state
        try:
            navigate_fn = next(iter(state.values()))
        except Exception:
            navigate_fn = None

    router_value = {current_path_only: navigate_fn}

    # Only render the first matching <Route> child
    def _matches(path_pattern: str, s: str, exact: bool) -> bool:
        import re

        # explicit regex
        if path_pattern.startswith("^"):
            return re.match(path_pattern, s) is not None

        # helper to convert route pattern to regex
        def to_regex(pat: str, exact_local: bool):
            tokens = []
            i = 0
            while i < len(pat):
                c = pat[i]
                if c == ":":
                    j = i + 1
                    while j < len(pat) and (pat[j].isalnum() or pat[j] == "_"):
                        j += 1
                    name = pat[i + 1 : j] or "param"
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

        # prefix with '/*'
        if path_pattern.endswith("/*"):
            rx = to_regex(path_pattern[:-1], exact_local=False)
            return rx.match(s) is not None

        rx = to_regex(path_pattern, exact_local=exact)
        return rx.match(s) is not None

    current_path_for_match = current_path_only.split("?")[0]

    selected_child = None
    for ch in children:
        # VNode has attribute 'component_fn' and 'props'
        component_fn = getattr(ch, "component_fn", None)
        props = getattr(ch, "props", None)
        if component_fn is None or props is None:
            continue
        if getattr(component_fn, "__name__", "") != "Route":
            continue
        path_pattern = props.get("path", "/")
        exact = props.get("exact", True)
        if _matches(path_pattern, current_path_for_match, exact):
            selected_child = ch
            break

    selected_children = [] if selected_child is None else [selected_child]

    return [RouterContext(value=router_value, children=selected_children)]
