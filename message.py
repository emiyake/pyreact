from pyreact.core import component, hooks, MessageBuffer
from typing import Literal
import itertools
import json
import sys
import time

# Monotonic counter for unique message IDs.
_msg_counter = itertools.count(1)


def _is_web_mode() -> bool:
    """Return True when stdout has been redirected by enable_web_print()."""
    return hasattr(sys.stdout, "_console")


@component
def Message(
    text: str,
    trigger: Literal["change", "mount"] = "change",
    sender: Literal["user", "system", "assistant"] = "user",
    message_type: Literal["chat", "info", "warning", "error", "log"] = "chat",
):
    """
    Unified output component for the framework.

    Covers all output cases:
    - "chat" / "info" / "warning" / "error": structured messages sent to
      the web bridge (MessageBuffer) AND printed to stdout (terminal only).
    - "log": lightweight terminal-only output (print to stdout).

    Each structured message carries a unique ``msg_id`` so the frontend
    can deduplicate replayed messages (e.g. on reconnect via "hello").

    Args:
        text: Message text
        trigger: When to trigger ("change" or "mount")
        sender: Who sent the message ("user", "system", "assistant")
        message_type: "chat", "info", "warning", "error", or "log"
    """
    deps = [] if trigger == "mount" else [text]

    def _send_message():
        if message_type == "log":
            # Log-only: print to stdout (in web mode goes to STDOUT channel)
            print(text)
            return

        # Structured message → web bridge
        console = MessageBuffer()

        message_data = {
            "type": "message",
            "msg_id": next(_msg_counter),
            "text": text,
            "sender": sender,
            "message_type": message_type,
            "timestamp": time.time(),
        }

        message_json = json.dumps(message_data)
        message_text = f"__MESSAGE__:{message_json}\n"

        # Sends to the console buffer (web bridge picks this up)
        console.append(message_text)

        # In terminal mode, also print so the user sees output.
        # In web mode, skip — the message already reaches the client
        # via the MSG channel; printing would duplicate it on STDOUT.
        if not _is_web_mode():
            print(text)

    hooks.use_effect(_send_message, deps)
    return []
