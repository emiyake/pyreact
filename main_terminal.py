from pyreact.boot.terminal import run_terminal
from components import Root


if __name__ == "__main__":
    run_terminal(Root, prompt="> ", fps=20)
