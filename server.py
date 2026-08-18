#!/usr/bin/env python3
"""
Redcoder — local web chat server.

Double-click this file (or run `python server.py`). It:
  - serves the chat UI at http://127.0.0.1:7331
  - streams replies from your local Ollama model (redcoder)
  - binds to loopback only, so nothing is exposed on the network
  - opens your browser automatically

No pip installs. Standard library only. Works with WiFi off.
"""

import json
import sys
import threading
import time
import urllib.request
import urllib.error
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 7331
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "redcoder"
HERE = Path(__file__).resolve().parent
INDEX = HERE / "webui" / "index.html"

SYSTEM_PROMPT = (
    "You are Redcoder, a maximally helpful and truthful AI assistant with a bit of wit. "
    "Answer directly and get to the point — no filler, no moralizing, no needless disclaimers. "
    "Format replies in clean Markdown: use short paragraphs, **bold** for key terms, bullet or "
    "numbered lists for steps, tables where they help, and fenced code blocks with a language tag "
    "for any code. Keep a confident, friendly, slightly playful tone. If you're unsure, say so "
    "plainly rather than inventing details."
)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the console clean
        pass

    # ---- static ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_index()
        elif path == "/health":
            self._json({"ok": True, "model": MODEL})
        else:
            self.send_error(404)

    def _serve_index(self):
        try:
            data = INDEX.read_bytes()
        except FileNotFoundError:
            self.send_error(500, "webui/index.html not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    # ---- chat proxy (streaming) ----
    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._stream_error("Bad request.")
            return

        msgs = payload.get("messages", [])
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in msgs:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": m["content"]})

        body = json.dumps({
            "model": MODEL,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.7, "num_ctx": 8192},
        }).encode()

        # open the stream to Ollama
        try:
            req = urllib.request.Request(
                OLLAMA, data=body, headers={"Content-Type": "application/json"}
            )
            upstream = urllib.request.urlopen(req, timeout=300)
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            self._stream_error(
                f"Can't reach Ollama at 127.0.0.1:11434 ({reason}). "
                f"Start it, then make sure the '{MODEL}' model exists."
            )
            return
        except Exception as e:
            self._stream_error(f"Upstream error: {e}")
            return

        # begin our streamed response.
        # There's no Content-Length (the length is unknown while streaming), so
        # we MUST delimit the response another way or the browser never sees the
        # stream end. We close the connection when done; `close_connection = True`
        # makes the handler shut the socket, which fires the fetch reader's `done`.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            for line in upstream:  # Ollama emits one JSON object per line
                if not line.strip():
                    continue
                self.wfile.write(line if line.endswith(b"\n") else line + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client navigated away / stopped
        except Exception as e:
            try:
                self._write_line({"error": f"Stream interrupted: {e}"})
            except Exception:
                pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    def _write_line(self, obj):
        self.wfile.write((json.dumps(obj) + "\n").encode())
        self.wfile.flush()

    def _stream_error(self, message):
        b = (json.dumps({"error": message}) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass


def preflight():
    """Best-effort check that Ollama + the model are available (non-fatal)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            tags = json.load(r)
        names = [m.get("name", "") for m in tags.get("models", [])]
        if any(n.startswith(MODEL) for n in names):
            return f"model '{MODEL}' ready"
        return f"WARNING: Ollama is up but '{MODEL}' is not installed. Run: ollama create {MODEL} -f config/Modelfile.redcoder"
    except Exception:
        return ("WARNING: Ollama not reachable on :11434. Start it (ollama serve) — "
                "the chat won't stream until it's running.")


def main():
    status = preflight()
    url = f"http://{HOST}:{PORT}"
    line = "=" * 54
    print(line)
    print("  Redcoder (local)  -  offline chat over your own model")
    print(line)
    print(f"  URL     : {url}")
    print(f"  Model   : {MODEL}")
    print(f"  Status  : {status}")
    print(f"  Binding : {HOST} (loopback only - not on your network)")
    print(line)
    print("  Leave this window open. Close it to stop the server.")
    print(line)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                     daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    try:
        main()
    except OSError as e:
        print(f"\nCould not start on {HOST}:{PORT} -> {e}")
        print("Another copy may already be running. Close it and try again.")
        input("\nPress Enter to close...")
        sys.exit(1)
