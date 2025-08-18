# pyreact/web/renderer.py
from typing import Any, Dict
import html as _htmllib

from pyreact.core.hook import HookContext


def _escape(s: Any) -> str:
    return _htmllib.escape("" if s is None else str(s), quote=True)


def _style_to_str(v: Any) -> str:
    """Convert a style dict to a CSS string.

    Example: ``{"font_size":"14px","background-color":"#fff"} -> "font-size:14px;background-color:#fff"``
    """
    if isinstance(v, dict):
        parts = []
        for k, val in v.items():
            k = k.replace("_", "-")
            parts.append(f"{k}:{val}")
        return ";".join(parts)
    return str(v)


def _attrs_to_str(props: Dict[str, Any]) -> str:
    """Convert props (excluding children/key/__internal) into HTML attributes.

    Rules:
      - ``class_`` -> ``class``
      - ``data_xxx`` -> ``data-xxx``
      - ``aria_xxx`` -> ``aria-xxx``
      - style dict -> ``style="k:v;..."``
      - ``True`` values -> boolean attributes (e.g., ``disabled``)
      - lists/tuples -> ``' '.join(...)``
    """
    if not props:
        return ""

    out = []
    for k, v in props.items():
        if k in ("children", "key", "__internal"):
            continue
        if v is None:
            continue

        # normalizations
        if k == "class_":
            k = "class"
        elif k.startswith("data_"):
            k = "data-" + k[5:].replace("_", "-")
        elif k.startswith("aria_"):
            k = "aria-" + k[5:].replace("_", "-")

        # values
        if isinstance(v, (list, tuple)):
            v = " ".join(map(str, v))
        elif k == "style":
            v = _style_to_str(v)

        # booleans as valueless attributes
        if v is True:
            out.append(k)
            continue
        if v is False:
            continue

        out.append(f'{k}="{_escape(v)}"')

    return (" " + " ".join(out)) if out else ""


def _render_node(ctx: HookContext) -> str:
    fn = ctx.component_fn

    # Text node (created by ``web/html.t(...)``)
    if getattr(fn, "__is_text_node__", False):
        val = ctx.props.get("value", "")
        return _escape(val)

    # HTML tag
    if getattr(fn, "__is_html_tag__", False):
        tag = getattr(fn, "__html_tag_name__", "div")
        attrs = _attrs_to_str(ctx.props)
        inner = "".join(_render_node(ch) for ch in ctx.children)
        return f"<{tag}{attrs}>{inner}</{tag}>"

    # Logical components (Router, Route, etc.) are transparent to HTML
    return "".join(_render_node(ch) for ch in ctx.children)


def render_to_html(root_ctx: HookContext) -> str:
    """Render the subtree of ``root_ctx`` (its children) into an HTML string."""
    return "".join(_render_node(ch) for ch in root_ctx.children)
