from integrations.dspy_integration import DSPyProvider, use_dspy_module
from integrations.use_dspy import use_dspy_call
from pyreact.boot import run_terminal, run_web
from pyreact.components.keystroke import Keystroke
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from pyreact.router import Route, Router, use_route
import dspy

import os

os.environ["OPENAI_API_KEY"] = "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"

UserContext = create_context(default="anonymous", name="User")

def use_user():
    user = hooks.use_context(UserContext)
    def _set(u):
        UserContext.set(u)
    return user, _set



class QASig(dspy.Signature):
    """Responda em uma frase, de forma direta."""
    question: str = dspy.InputField()
    answer: str   = dspy.OutputField()

@component
def Print(text: str):
    hooks.use_effect(lambda: print(text), [text])
    return []


@component
def QAPage():
    qa_mod = use_dspy_module(QASig, dspy.ChainOfThought, name="qa-cot")

    run, result, loading, error = use_dspy_call(qa_mod)

    last_q, set_last_q = hooks.use_state("")

    def on_enter(line: str):
        # dispara inferência ao teclar Enter
        if not line.strip():
            return
        set_last_q(line)
        print("RUN", line)
        run(question=line)

    print("RES", result)

    # Derivações simples do resultado/erro
    answer = getattr(result, "answer", None) if result is not None else None
    err    = str(error) if error else None

    # UI: um input de terminal + logs reativos
    children = [
        Print(key="hint", text="Digite sua pergunta e pressione Enter…"),
        Keystroke(key="qa_input", path="/qa", exclusive=True, on_submit=on_enter),
    ]
    if last_q:
        children.append(Print(key="q", text=f"Q: {last_q}"))
    if loading:
        children.append(Print(key="load", text="Consultando o modelo…"))
    if answer:
        children.append(Print(key="ans", text=f"A: {answer}"))
    if err:
        children.append(Print(key="err", text=f"Erro: {err}"))

    return children



@component
def Text(text: str):
    hooks.use_effect(lambda: print(text), [])
    return []


@component
def Link(to, label):
    _, navigate = use_route()

    def handle(k):
        if k == label[0].lower():
            print("To: ", to)
            navigate(to)

    return [Keystroke(on_submit=handle)]

@component
def Home():
    return [Text(key="h", text="🏠 Home")]

@component
def About():
    return [Text(key="a", text="ℹ️  About")]

@component
def NotFound():
    return [Text(key="404", text="404 – not found")]

@component
def App():
    return [
        Router(
            initial="/qa",
            children=[
                Route(key="r1", path="/home",          children=[Home(key="home")]),
                Route(key="r2", path="/about",     children=[About(key="about")]),
                Route(key="r3", path="/qa",    children=[QAPage(key="qa")]),
            ],
        )
    ]

@component
def Root():
    # Configure seu LM uma única vez
    # Exemplo: dspy.settings.configure(lm=...) também funciona;
    # aqui passo via Provider para ficar explícito.
    lm = dspy.LM("openai/gpt-4o-mini")   # ✅ correto no DSPy 3.x
    return [DSPyProvider(key="dspy", lm=lm, children=[App(key="app")])]

if __name__ == "__main__":
    run_terminal(Root, prompt="> ", fps=20)
    #run_web(App, host="127.0.0.1", port=8000)
