from __future__ import annotations

from pathlib import Path

# Base HTML template used for SSR. Prefer loading from static/base.html,
# fallback to sibling base.html, then to the inline template in dev.
try:
    BASE_HTML = (Path(__file__).with_name("static") / "base.html").read_text(
        encoding="utf-8"
    )
except Exception:
    BASE_HTML = """
      <!doctype html>
      <html>
        <head>
          <meta charset="utf-8" />
          <title>Template not found</title>
        </head>
        <body>
          <p>base.html was not found. Place the file at /static/base.html.</p>
        </body>
      </html>
      """
