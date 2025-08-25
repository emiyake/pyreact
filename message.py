from pyreact.core.core import component, hooks
from pyreact.web.console import ConsoleBuffer
from typing import Literal
import json
import time


@component
def Message(
    text: str,
    trigger: Literal["change", "mount"] = "change",
    sender: Literal["user", "system", "assistant"] = "user",
    message_type: Literal["chat", "info", "warning", "error"] = "chat",
):
    """
    Message component that renders as a chat bubble.

    Args:
        text: Message text
        trigger: When to trigger ("change" or "mount")
        sender: Who sent the message ("user", "system", "assistant")
        message_type: Message type ("chat", "info", "warning", "error")
    """
    deps = [] if trigger == "mount" else [text]

    def _send_message():
        # Sends the message to the console buffer with special formatting
        console = ConsoleBuffer()

        # Creates a structured message object
        message_data = {
            "type": "message",
            "text": text,
            "sender": sender,
            "message_type": message_type,
            "timestamp": time.time(),
        }

        # Always send as JSON to be processed by the system
        message_json = json.dumps(message_data)
        message_text = f"__MESSAGE__:{message_json}\n"

        # Sends to the console buffer
        console.append(message_text)

        # To ensure it works in the terminal, also prints directly
        import sys

        try:
            # Tries to print directly to the original terminal
            sender_colors = {
                "user": "\x1b[34m",  # Blue
                "system": "\x1b[90m",  # Gray
                "assistant": "\x1b[32m",  # Green
            }
            type_colors = {
                "chat": "",
                "info": "\x1b[36m",  # Cyan
                "warning": "\x1b[33m",  # Yellow
                "error": "\x1b[31m",  # Red
            }

            color = sender_colors.get(sender, "") + type_colors.get(message_type, "")
            reset = "\x1b[0m"

            formatted_text = f"{color}[{sender.upper()}] {text}{reset}\n"

            # Tries different methods to print to the terminal
            if hasattr(sys.stdout, "_original") and sys.stdout._original is not None:
                sys.stdout._original.write(formatted_text)
                sys.stdout._original.flush()
            else:
                # Fallback: print directly
                print(formatted_text, end="", flush=True)
        except Exception:
            # If it fails, use normal print
            print(f"[{sender.upper()}] {text}")

    hooks.use_effect(_send_message, deps)
    return []
