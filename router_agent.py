from typing import List, Dict, Optional, Any
import dspy

from pyreact.core.core import component, hooks
from pyreact.router import use_route, use_routes_catalog
from integrations.use_dspy import use_dspy_call
from integrations.dspy_integration import use_dspy_module
from log import Log


def _is_parametrized(path: str) -> bool:
    return ":" in path or path.endswith("*")


@component
def RouterAgent(
    message: str,
    *,
    routes_meta: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = "fast",
    children=None,
):
    """
    Decide a route from a free-text `message` and navigate to it.

    - Read available routes from context via `use_routes_catalog()`.
    - Optionally merge additional metadata: description, utterances, default params.
    - If a `resolver(message, candidates)` is provided, its return (path) wins.
    - Otherwise, apply heuristic scoring against names, paths, descriptions, utterances.

    Example `routes_meta` item:
        {
          "path": "/about",
          "name": "about",
          "description": "Information about the app",
          "utterances": ["go to about", "open about"],
          "params": {"id": "1"}
        }
    """
    current_full, navigate = use_route()
    catalog = use_routes_catalog() or []
    meta_list = routes_meta or []

    by_path = {str(m.get("path")): m for m in meta_list if m.get("path")}
    by_name = {str(m.get("name")): m for m in meta_list if m.get("name")}

    candidates: List[Dict[str, Any]] = []
    for r in catalog:
        path = r.get("path", "/")
        name = r.get("name") or path
        exact = bool(r.get("exact", True))
        base_desc = r.get("description", "")
        base_utts = r.get("utterances", []) or []
        base_params = r.get("params", None) or r.get("default_params", None)

        m = by_path.get(path) or by_name.get(name) or {}
        desc = m.get("description", base_desc)
        utts = m.get("utterances", base_utts) or []
        params = m.get("params", base_params)

        candidates.append(
            {
                "path": path,
                "name": name,
                "exact": exact,
                "description": desc,
                "utterances": utts,
                "params": params,
            }
        )

    state, set_state = hooks.use_state(
        {"last_message": None, "last_ver": None, "requested_for": None}
    )

    # No local resolver/heuristics: delegate selection to the LLM

    # ---------------- LLM module (optional) -----------------
    class RouteSelectionSig(dspy.Signature):
        """Choose the best route for the message.

        Instructions:
        - Return ONLY the exact path of one of the provided routes.
        - If nothing is appropriate, return None.
        - Consider name, description, and utterances.
        """

        message: str = dspy.InputField()
        routes_text: str = dspy.InputField()
        path: str = dspy.OutputField()

    choose_mod = use_dspy_module(
        RouteSelectionSig, dspy.ChainOfThought, name="router-agent"
    )
    call_llm, llm_result, llm_loading, llm_error = use_dspy_call(
        choose_mod, model=model
    )

    def _routes_text(lst: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for i, e in enumerate(lst, start=1):
            nm = e.get("name") or ""
            pt = e.get("path") or ""
            ds = (e.get("description") or "").strip()
            uts = ", ".join((e.get("utterances") or [])[:4])
            param_note = (
                " (needs params)"
                if _is_parametrized(pt) and not e.get("params")
                else ""
            )
            lines.append(
                f"{i}. name={nm} path={pt}{param_note}\n   desc={ds}\n   utts=[{uts}]"
            )
        return "\n".join(lines)

    # --------------- Decide and navigate -------------------
    def _effect_decide():
        if not isinstance(message, str) or not message.strip():
            return
        # Avoid repeated LLM calls for the same message during re-renders
        if state.get("requested_for") == message:
            return
        set_state(
            {
                "last_message": state.get("last_message"),
                "last_ver": state.get("last_ver"),
                "requested_for": message,
            }
        )
        call_llm(message=message, routes_text=_routes_text(candidates))

    hooks.use_effect(_effect_decide, [message, str(catalog), str(routes_meta)])

    def _effect_llm_nav():
        if llm_result is None:
            return
        mod_res, ver = llm_result
        if state.get("last_ver") == ver:
            return
        path_raw = getattr(mod_res, "path", None)
        if isinstance(path_raw, str):
            # Strip code fences/quotes/spaces just in case
            path_clean = path_raw.strip().strip('`" ')
        else:
            path_clean = None

        target_path: Optional[str] = None
        params_to_use: Optional[Dict[str, Any]] = None

        # Accept only known candidate paths or '/'
        paths = {e.get("path"): e for e in candidates}
        if path_clean in paths:
            target_path = path_clean
            params_to_use = paths[path_clean].get("params")
        elif path_clean == "/":
            target_path = "/"
            params_to_use = None

        set_state({"last_message": message, "last_ver": ver, "requested_for": None})
        if target_path is not None:
            current_base = current_full.split("?")[0]
            if current_base != target_path:
                print(f"Navigating to {target_path}")
                navigate(target_path, params=params_to_use)

    hooks.use_effect(_effect_llm_nav, [llm_result, message, current_full])

    if llm_loading:
        return [Log(text="Router agent, verificando rota...")]

    return children or []
