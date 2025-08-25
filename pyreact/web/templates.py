from __future__ import annotations

# Base HTML template used for SSR. Kept separate to respect SRP and ease reuse/testing.
BASE_HTML = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Reaktiv App</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <style>
      html,body{margin:0;padding:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu}
      
      /* Chat message styles */
      .chat-message {
        display: flex;
        margin-bottom: 8px;
        animation: fadeIn 0.3s ease-in;
      }
      
      .chat-message.user {
        justify-content: flex-end;
      }
      
      .chat-message.system {
        justify-content: center;
      }
      
      .chat-message.assistant {
        justify-content: flex-start;
      }
      
      .message-bubble {
        max-width: 70%;
        padding: 12px 16px;
        border-radius: 18px;
        word-wrap: break-word;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
      }
      
      .chat-message.user .message-bubble {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-bottom-right-radius: 4px;
      }
      
      .chat-message.assistant .message-bubble {
        background: #f1f5f9;
        color: #1e293b;
        border: 1px solid #e2e8f0;
        border-bottom-left-radius: 4px;
      }
      
      .chat-message.system .message-bubble {
        background: #fef3c7;
        color: #92400e;
        border: 1px solid #fde68a;
        font-size: 0.9em;
        font-style: italic;
      }
      
      .message-bubble.info {
        background: #dbeafe !important;
        color: #1e40af !important;
        border-color: #93c5fd !important;
      }
      
      .message-bubble.warning {
        background: #fef3c7 !important;
        color: #92400e !important;
        border-color: #fde68a !important;
      }
      
      .message-bubble.error {
        background: #fee2e2 !important;
        color: #991b1b !important;
        border-color: #fca5a5 !important;
      }
      
      .message-sender {
        font-size: 0.75em;
        margin-bottom: 4px;
        opacity: 0.7;
        font-weight: 500;
      }
      
      .log-entry {
        background: #0b1020;
        color: #e7f0ff;
        padding: 8px 12px;
        font-family: monospace;
        font-size: 12px;
        line-height: 1.4;
        margin: 4px 0;
        animation: fadeIn 0.3s ease-in;
        white-space: pre-wrap;
        word-wrap: break-word;
        overflow-x: auto;
      }

      .log-entry:first-child {
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
      }

      .log-entry:last-child {
        border-bottom-left-radius: 6px;
        border-bottom-right-radius: 6px;
      }

      .log-entry + .log-entry {
        margin: 0;
      }
      
      @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }
    </style>
  </head>
  <body class="m-0 p-0 font-sans">
    <div id="dbg"
      class="fixed top-0 left-0 right-0 flex gap-2 items-center py-2 px-3 bg-[#f7f7f8] border-b border-[#e5e7eb] z-10">
      <button id="dbg-tree"
        class="px-3 py-1 border border-gray-300 rounded-[6px] bg-white cursor-pointer hover:bg-gray-100 transition-colors">
        Print VNode Tree (Ctrl+V)
      </button>
      <button id="dbg-trace"
        class="px-3 py-1 border border-gray-300 rounded-[6px] bg-white cursor-pointer hover:bg-gray-100 transition-colors">
        Print Render Trace (Ctrl+T)
      </button>
      <label
        class="inline-flex items-center gap-2 text-[14px]">
        <input type="checkbox" id="dbg-trace-enable" /> enable tracing
      </label>
    </div>
    <div id="chat-container" class="mt-[60px] mx-4 mb-4 max-h-[80vh] overflow-auto">
      <div id="chronological-output" class="space-y-2"></div>
    </div>
    <div id="root" class="p-4">{SSR}</div>
    <input id="cli"
      class="fixed bottom-0 left-0 right-0 py-2 px-4 border-0 border-t border-gray-300 text-[16px] outline-none"
      placeholder="type and press Enter…" autofocus />
    <script src="/static/app.js"></script>
  </body>
</html>"""
