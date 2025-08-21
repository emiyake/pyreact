from pyreact.boot import run_web
from components import Root


if __name__ == "__main__":
    run_web(Root, host="127.0.0.1", port=8000)
