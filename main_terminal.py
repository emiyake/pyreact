from pyreact.boot import bootstrap
from pyreact.core.core import component
from components import Root
import os
import dotenv
import dspy


@component
def Boot(input=""):
    dotenv.load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    lm_default = dspy.LM("openai/gpt-4o", api_key=api_key)
    lm_fast = dspy.LM("openai/gpt-4o-mini", api_key=api_key)
    models = {
        "default": lm_default,
        "fast": lm_fast,
        "reasoning": lm_default,
    }
    return [Root(key="root", models=models, input=input)]


if __name__ == "__main__":
    myapp = bootstrap(Boot, fps=20)

    while True:
        try:
            question = input("> ")
        except (KeyboardInterrupt, EOFError):
            break

        if question.strip() in (":q", ":quit", ":exit"):
            break

        if not question.strip():
            continue

        try:
            result = myapp(question)
            if result is not None:
                print(result)
        except TimeoutError as e:
            print(f"[timeout] {e}")
        except Exception as e:
            print(f"[error] {e}")

    myapp.shutdown()
