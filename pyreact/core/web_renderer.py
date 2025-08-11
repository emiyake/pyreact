import html as _htmllib
from .hook import HookContext  # seu HookContext

def _escape(s: str) -> str:
    return _htmllib.escape(s, quote=True)

def _attrs_to_str(props: dict) -> str:
    if not props:
        return ""
    # mapeia class_ -> class, data_test -> data-test, etc, se quiser
    norm = {}
    for k, v in props.items():
        if k in ("children", "key", "__internal"):
            continue
        if v is None:
            continue
        # convenção: class_ vira class
        if k == "class_":
            k = "class"
        # data_foo -> data-foo (opcional)
        if k.startswith("data_"):
            k = "data-" + k[5:].replace("_", "-")
        norm[k] = v

    if not norm:
        return ""
    parts = [f'{k}="{_escape(str(v))}"' for k, v in norm.items()]
    return " " + " ".join(parts)

def _render_node(ctx: HookContext) -> str:
    fn = ctx.component_fn

    # nó de texto
    if getattr(fn, "__is_text_node__", False):
        val = ctx.props.get("value", "")
        return _escape(str(val))

    # tag HTML
    if getattr(fn, "__is_html_tag__", False):
        tag = getattr(fn, "__html_tag_name__", "div")
        attrs = _attrs_to_str(ctx.props)
        inner = "".join(_render_node(ch) for ch in ctx.children)
        return f"<{tag}{attrs}>{inner}</{tag}>"

    # componente "lógico" (Router, Route, etc.) — transparente
    return "".join(_render_node(ch) for ch in ctx.children)

def render_to_html(root_ctx: HookContext) -> str:
    # renderiza apenas os filhos do root (root é a App)
    return "".join(_render_node(ch) for ch in root_ctx.children)