import html as _htmllib
from .hook import HookContext  # HookContext

def _escape(s: str) -> str:
    return _htmllib.escape(s, quote=True)

def _attrs_to_str(props: dict) -> str:
    if not props:
        return ""
    # map class_ -> class, data_test -> data-test, etc, if desired
    norm = {}
    for k, v in props.items():
        if k in ("children", "key", "__internal"):
            continue
        if v is None:
            continue
        # convention: class_ becomes class
        if k == "class_":
            k = "class"
        # data_foo -> data-foo (optional)
        if k.startswith("data_"):
            k = "data-" + k[5:].replace("_", "-")
        norm[k] = v

    if not norm:
        return ""
    parts = [f'{k}="{_escape(str(v))}"' for k, v in norm.items()]
    return " " + " ".join(parts)

def _render_node(ctx: HookContext) -> str:
    fn = ctx.component_fn

    # text node
    if getattr(fn, "__is_text_node__", False):
        val = ctx.props.get("value", "")
        return _escape(str(val))

    # HTML tag
    if getattr(fn, "__is_html_tag__", False):
        tag = getattr(fn, "__html_tag_name__", "div")
        attrs = _attrs_to_str(ctx.props)
        inner = "".join(_render_node(ch) for ch in ctx.children)
        return f"<{tag}{attrs}>{inner}</{tag}>"

    # logical component (Router, Route, etc.) — transparent
    return "".join(_render_node(ch) for ch in ctx.children)

def render_to_html(root_ctx: HookContext) -> str:
    # render only the children of the root (root is the App)
    return "".join(_render_node(ch) for ch in root_ctx.children)

