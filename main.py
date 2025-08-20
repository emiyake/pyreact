from integrations.dspy_integration import DSPyProvider, use_dspy_module
from integrations.use_dspy import use_dspy_call
from pyreact.boot import run_web, run_terminal
from pyreact.components.keystroke import Keystroke
from pyreact.core.core import component, hooks
from pyreact.core.provider import create_context
from pyreact.router import Route, Router, use_route, use_navigate, use_query_params
import dspy

from pyreact.router.route import use_route_params
import os
import dotenv


dotenv.load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


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
    answer: str = dspy.OutputField()


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
    check_toxicity, result_toxicity, loading, _ = use_dspy_call(toxicity, model="fast")

    def _check_toxicity():
        if not question.strip():
            return
        check_toxicity(comment=question)

    path, navigate = use_route()

    hooks.use_effect(_check_toxicity, [question])
    hooks.use_effect(
        lambda: set_ver(result_toxicity[1] if result_toxicity else 0), [result_toxicity]
    )

    if result_toxicity is None or ver != (result_toxicity or [None, None])[1]:
        return []

    if loading:
        return [Print(key="loading", text="Consultando o modelo…")]

    if getattr(result_toxicity[0], "toxic", False):
        navigate("/home/3")
        return []
        return [
            Print(
                key=f"toxic-{result_toxicity[1]}",
                text="The question is considered toxic",
            )
        ]
    else:
        return children


@component
def QAAgent(question: str):
    # qa_mod = dspy.Predict(QASig)
    qa_mod = use_dspy_module(QASig, dspy.ChainOfThought, name="qa-cot")
    call_dspy, result, loading, error = use_dspy_call(qa_mod, model="reasoning")

    def _call_dspy():
        if not question.strip():
            return
        call_dspy(question=question)

    hooks.use_effect(_call_dspy, [question])

    if loading:
        print("Carregando... Aguarde.")

    if error:
        return [Print(key="error", text=f"Error: {error}")]

    if result is None:
        return []

    return [Print(key="agent", text=f"Response: {getattr(result[0], 'answer', None)}")]


@component
def QAHome():
    last_message, set_last_message = hooks.use_state("")

    def on_enter(line: str):
        if not line.strip():
            return
        set_last_message(line)

    return [
        Print(key="hint", text="Digite sua pergunta e pressione Enter…"),
        Keystroke(key="qa_input", on_submit=on_enter),
        GuardRail(
            key="guardrail",
            question=last_message,
            children=[QAAgent(key="agent", question=last_message)],
        ),
    ]


@component
def Text(text: str):
    hooks.use_effect(lambda: print(text), [])
    return []


@component
def Home():
    route_params = use_route_params()
    query_params = use_query_params()
    navigate = use_navigate()

    print("Route params:", route_params)
    print("Query params:", query_params)

    def handle_navigate_with_params(k):
        if k == "a":
            navigate(
                "/about", params={"id": "457"}, query={"tab": "profile", "edit": "true"}
            )
        elif k == "q":
            navigate("/qa")
        elif k == "d":
            navigate(
                {
                    "path": "/home/:id",
                    "params": {"id": "789"},
                    "query": {"mode": "debug", "level": "info"},
                    "fragment": "section1",
                }
            )

    id_text = (
        f" (ID: {route_params.get('id', 'none')})" if route_params.get("id") else ""
    )
    query_text = f" Query: {query_params}" if query_params else ""

    return [
        Text(key="h", text=f"🏠 Home{id_text}{query_text}"),
        Text(
            key="help", text="Press 'a' for about, 'q' for qa, 'd' for dict navigation"
        ),
        Keystroke(key="nav", on_submit=handle_navigate_with_params),
    ]


@component
def About():
    query_params = use_query_params()
    navigate = use_navigate()

    def handle_navigation(k):
        if k == "h":
            navigate("/home/about-redirect", query={"from": "about"})
        elif k == "s":
            navigate("/about", query={"search": "documentation", "filter": "recent"})

    search_text = (
        f" (Search: {query_params.get('search', 'none')})"
        if query_params.get("search")
        else ""
    )
    filter_text = (
        f" Filter: {query_params.get('filter', 'none')}"
        if query_params.get("filter")
        else ""
    )

    return [
        Text(key="a", text=f"ℹ️  About{search_text}{filter_text}"),
        Text(
            key="help2",
            text="Press 'h' to go home with params, 's' to add search query",
        ),
        Keystroke(key="about-nav", on_submit=handle_navigation),
    ]


@component
def NotFound():
    return [Text(key="404", text="404 – not found")]


@component
def App():
    return [
        Router(
            initial="/qa",
            children=[
                Route(key="r1", path="/home/:id", children=[Home(key="home")]),
                Route(key="r2", path="/about", children=[About(key="about")]),
                Route(key="r3", path="/qa", children=[QAHome(key="qa")]),
                Route(key="r4", path="/", children=[Home(key="home")]),
            ],
        )
    ]


@component
def Root():
    lm_default = dspy.LM("openai/gpt-4o", api_key=OPENAI_API_KEY)
    lm_fast = dspy.LM("openai/gpt-4o-mini", api_key=OPENAI_API_KEY)
    models = {
        "default": lm_default,
        "fast": lm_fast,
        "reasoning": lm_default,
    }
    return [DSPyProvider(key="dspy", models=models, children=[App(key="app")])]


if __name__ == "__main__":
    # run_terminal(Root, prompt="> ", fps=20)
    run_web(Root, host="127.0.0.1", port=8000)
