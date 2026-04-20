#!/usr/bin/env python3
"""Golden Bank local dev server.

Serves index.html and proxies chat messages to the Google CES API.
Auth is handled server-side via gcloud — no browser OAuth needed.

Also exposes a Google ADK-compatible API so nuguard behavior can target this
server directly:
  POST /apps/{app_name}/users/{user_id}/sessions  → create session
  POST /run                                        → run a turn (ADK format)
  GET  /list-apps                                  → return app name list

Usage:
    gcloud auth login          # one-time
    python3 serve.py           # default port 8088
    python3 serve.py 8080      # custom port
"""
from __future__ import annotations

import http.server
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
ROOT = Path(__file__).parent

PROJECT      = "platform-dev-2025"
APP_ID       = "2f519af5-a66e-40a4-ad77-b52cb9a96394"
VERSION      = "9c99ba4b-fcec-4466-96c7-950e111bd239"
DEPLOY       = "4c8a5e8b-4bfd-46b4-9001-4933ab2b3b2f"
ADK_APP_NAME = "golden_bank_agent"
CES_BASE     = f"https://ces.googleapis.com/v1beta/projects/{PROJECT}/locations/us/apps/{APP_ID}"
# Resource name prefix used in CES request body fields (no https:// prefix)
_APP_RESOURCE = f"projects/{PROJECT}/locations/us/apps/{APP_ID}"

# Pattern for ADK session-creation path
_SESSION_PATH_RE = re.compile(r"^/apps/[^/]+/users/[^/]+/sessions$")


def _access_token() -> str:
    """Return a short-lived access token via gcloud."""
    for cmd in (
        ["gcloud", "auth", "print-access-token"],
        ["gcloud", "auth", "application-default", "print-access-token"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            t = r.stdout.strip()
            if t and not r.returncode:
                return t
        except Exception:
            pass
    raise RuntimeError("No Google credentials. Run: gcloud auth login")


def _run_session(session_id: str, message: str) -> str:
    """Send one turn to the CES agent and return the reply text."""
    token = _access_token()
    url   = f"{CES_BASE}/sessions/{session_id}:runSession"
    body  = json.dumps({
        "config": {
            "session":      f"{_APP_RESOURCE}/sessions/{session_id}",
            "app_version":  f"{_APP_RESOURCE}/versions/{VERSION}",
            "deployment":   f"{_APP_RESOURCE}/deployments/{DEPLOY}",
        },
        "inputs": [{"text": message}],
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    for output in data.get("outputs", []):
        text = output.get("text", "").strip()
        if text:
            return text
    return "(no response)"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path == "/chat":
            self._handle_chat()
        elif self.path == "/run":
            self._handle_adk_run()
        elif _SESSION_PATH_RE.match(self.path):
            self._handle_adk_create_session()
        else:
            print(f"  404   POST {self.path}  (no handler matched)")
            self.send_error(404)

    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/list-apps":
            self._json_ok({"apps": [ADK_APP_NAME]})
            return
        # /docs and /openapi.json are probed by nuguard for framework detection.
        # Return minimal stubs so they don't 404-crash and pollute the log.
        if self.path in ("/docs", "/openapi.json", "/redoc"):
            self._json_ok({"info": {"title": ADK_APP_NAME}})
            return
        super().do_GET()

    # ── Browser chat endpoint ────────────────────────────────────────────────

    def _handle_chat(self):
        payload    = self._read_json()
        session_id = payload.get("session_id", "default-session")
        message    = payload.get("message", "")
        try:
            reply = _run_session(session_id, message)
            self._json_ok({"reply": reply})
        except urllib.error.HTTPError as exc:
            err = exc.read().decode()[:400]
            self._error(f"CES API error {exc.code}: {err}")
        except Exception as exc:
            self._error(str(exc))

    # ── ADK-compatible endpoints for nuguard behavior ────────────────────────

    def _handle_adk_create_session(self):
        """POST /apps/{app}/users/{uid}/sessions → {"id": "<uuid>"}"""
        session_id = str(uuid.uuid4())
        self._json_ok({"id": session_id, "appName": ADK_APP_NAME})

    def _handle_adk_run(self):
        """POST /run with ADK RunAgentRequest → ADK Event[] response."""
        payload    = self._read_json()
        session_id = payload.get("sessionId", str(uuid.uuid4()))
        app_name   = payload.get("appName", "")
        # Extract user text from newMessage.parts[*].text
        new_msg  = payload.get("newMessage", {})
        parts    = new_msg.get("parts", [])
        message  = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
        print(f"  /run  app={app_name!r} session={session_id[:8]}… msg={message[:80]!r}")
        if not message:
            print("  /run  WARNING: empty message — returning empty event list")
            self._json_ok([])
            return

        try:
            reply = _run_session(session_id, message)
            print(f"  /run  reply={reply[:80]!r}")
            # Return minimal ADK Event[] so the behaviorner can parse it
            events = [
                {
                    "author": ADK_APP_NAME,
                    "content": {"role": "model", "parts": [{"text": reply}]},
                }
            ]
            self._json_ok(events)
        except urllib.error.HTTPError as exc:
            err = exc.read().decode()[:400]
            print(f"  /run  ERROR CES {exc.code}: {err}")
            self._error(f"CES API error {exc.code}: {err}")
        except Exception as exc:
            print(f"  /run  ERROR: {exc}")
            self._error(str(exc))

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _json_ok(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg: str):
        body = json.dumps({"error": msg}).encode()
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        # log_request:  args = (request_line_str, status_str, size_str)
        # log_error:    args = (code_int_or_HTTPStatus, message_str)
        first = str(args[0]) if args else ""
        # Suppress noise from well-known browser probes
        path = getattr(self, "path", "")
        if any(p in path for p in (".well-known", "favicon", "/docs", "/openapi", "/redoc")):
            return
        # log_error path: args[0] is a numeric code, args[1] is the message
        if args and not isinstance(args[0], str):
            code = int(args[0])
            msg  = str(args[1]) if len(args) > 1 else ""
            print(f"  {code}   {self.command} {path}  ({msg})")
            return
        # log_request path: args[1] is the status code string (e.g. "404")
        status = str(args[1]) if len(args) > 1 else ""
        if any(p in first for p in ("/chat", "/run", "/apps/", "/list-apps")):
            print(f"  api   {status} {first[:80]}")
        else:
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print(f"Golden Bank server: http://localhost:{PORT}")
    print(f"Chat API:           POST http://localhost:{PORT}/chat")
    print(f"ADK API:            POST http://localhost:{PORT}/run")
    print("Press Ctrl+C to stop.\n")
    with http.server.ThreadingHTTPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
