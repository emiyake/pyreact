# pyreact/input/focus.py

from pyreact.core.hook import HookContext

class _InputFocus:
    def __init__(self):
        self.current = None
    def acquire(self, token: str): self.current = token
    def release(self, token: str):
        if self.current == token:
            self.current = None
    def is_current(self, token: str) -> bool:
        return self.current == token

def get_focus():
    return HookContext.get_service("input_focus", _InputFocus)