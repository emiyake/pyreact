from pyreact.boot import bootstrap
from pyreact.core.core import component
from pyreact.web.server import create_fastapi_app
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
    import uvicorn

    myapp = bootstrap(Boot, fps=20)
    fastapi_app, _ = create_fastapi_app(myapp)
    uvicorn.run(fastapi_app, host="127.0.0.1", port=8000)
