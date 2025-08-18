from audioop import lin2adpcm
from webbrowser import get
from integrations.dspy_integration import DSPyProvider, use_dspy_module
from integrations.use_dspy import use_dspy_call
from pyreact.boot import run_terminal, run_web
from pyreact.components.keystroke import Keystroke
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from pyreact.router import Route, Router, use_route
import dspy

import os


UserContext = create_context(default="anonymous", name="User")

def use_user():
    user = hooks.use_context(UserContext)
    def _set(u):
        UserContext.set(u)
    return user, _set


class ConvertDates(dspy.Signature):
    """Extract dates from a sentence"""
    sentence: str = dspy.InputField()
    date_from: str = dspy.OutputField()
    date_to: str = dspy.OutputField()


class QASig(dspy.Signature):
    """Responda em uma frase, de forma direta."""
    question: str = dspy.InputField()
    answer: str   = dspy.OutputField()

@component
def Print(text: str):
    hooks.use_effect(lambda: print(text), [text])
    return []

@component
def GuardRail(question, children): 
  ver, set_ver = hooks.use_state(0)
  toxicity = dspy.Predict(
    dspy.Signature(
        "comment -> toxic: bool",
        instructions="Mark as 'toxic' if the comment includes insults, harassment, or sarcastic derogatory remarks.",
    )
  )
  check_toxicity, result_toxicity, loading,_ = use_dspy_call(toxicity)

  def _check_toxicity():
    if not question.strip():
      return
    check_toxicity(comment=question)

  hooks.use_effect(_check_toxicity, [question])
  hooks.use_effect(lambda: set_ver(result_toxicity[1] if result_toxicity else 0), [result_toxicity])

  if result_toxicity is None or ver == (result_toxicity or [None, None])[1]:
    return []

  if loading: 
    return [Print(key="loading", text="Consultando o modelo…")]
  


  if getattr(result_toxicity[0], "toxic", False):
    return [Print(key=f"toxic-{result_toxicity[1]}", text="A pergunta é considerada tóxica")]
  else:
    return children

@component
def QAAgent(question: str):
  # qa_mod = dspy.Predict(QASig)
  qa_mod = use_dspy_module(QASig, dspy.ChainOfThought, name="qa-cot")
  call_dspy, result, loading, error = use_dspy_call(qa_mod)

  def _call_dspy():
    if not question.strip():
      return
    call_dspy(question=question)

  hooks.use_effect(_call_dspy, [question])

  if loading:
    print("Carregando... Aguarde.")

  if result is None:
    return []



  return [Print(key="agent", text=f"Response: {getattr(result[0], 'answer', None)}")]


@component
def QAHome():
  qa_mod = use_dspy_module(ConvertDates, dspy.Predict, name="qa-cot")
  call_dspy, result, loading, error = use_dspy_call(qa_mod)

  last_q, set_last_q = hooks.use_state("")

  def on_enter(line: str):
      # dispara inferência ao teclar Enter
      if not line.strip():
          return
      set_last_q(line)
      # call_dspy(sentence=line)


  # Derivações simples do resultado/erro
  answer = getattr(result, "answer", None) if result is not None else None
  err    = str(error) if error else None

  # UI: um input de terminal + logs reativos
  children = [
      Print(key="hint", text="Digite sua pergunta e pressione Enter…"),
      Keystroke(key="qa_input", path="/qa", exclusive=True, on_submit=on_enter),
      GuardRail(key="guardrail", question=last_q, children=[
        QAAgent(key="agent", question=last_q)
      ])
  ]

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
                Route(key="r1", path="/home",      children=[Home(key="home")]),
                Route(key="r2", path="/hiring",     children=[About(key="hiring")]),
                Route(key="r3", path="/qa",        children=[QAHome(key="qa")]),
            ],
        )
    ]

@component
def Root():
    # Configure seu LM uma única vez
    # Exemplo: dspy.settings.configure(lm=...) também funciona;
    # aqui passo via Provider para ficar explícito.
    lm = dspy.LM("openai/gpt-4o-mini")
    return [DSPyProvider(key="dspy", lm=lm, children=[App(key="app")])]

if __name__ == "__main__":
    run_terminal(Root, prompt="> ", fps=20)
    # run_web(Root, host="127.0.0.1", port=8000)
