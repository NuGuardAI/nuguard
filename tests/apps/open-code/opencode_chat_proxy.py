#!/usr/bin/env python3
"""Single-turn chat proxy in front of a real `opencode serve` instance.

nuguard's behavior/redteam target contract sends one flat HTTP request per
turn (POST <endpoint> {<chat_payload_key>: "..."}) and expects the reply text
back under <chat_response_key>. OpenCode's own HTTP API has no such endpoint —
it's session based (see https://opencode.ai/docs/server):

    POST /session               -> { id, ... }
    POST /session/:id/message   -> { info, parts }   (parts include the reply text)

This proxy adapts one to the other so nuguard can target it like any other
chat app:

    POST /api/chat {"message": "..."}  -> {"response": "...", "session_id": "..."}

Usage:
    OPENCODE_URL=http://127.0.0.1:4096 python opencode_chat_proxy.py

Config (env vars):
    OPENCODE_URL            Base URL of the running `opencode serve` instance.
    OPENCODE_SERVER_PASSWORD  HTTP basic-auth password, if the server was
                              started with one set (username defaults to
                              "opencode", or OPENCODE_SERVER_USERNAME).
    OPENCODE_PROVIDER_ID    Optional providerID to pass with each message.
    OPENCODE_MODEL_ID       Optional modelID to pass with each message.
    PROXY_HOST / PROXY_PORT Where this proxy listens (default 127.0.0.1:8787).
    SESSION_PER_REQUEST     "true" to create a fresh session per HTTP request
                            (isolated turns, e.g. per-scenario in redteam);
                            "false" (default) reuses one session for the life
                            of the proxy process (multi-turn conversation).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPENCODE_URL = os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096").rstrip("/")
OPENCODE_SERVER_USERNAME = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
OPENCODE_SERVER_PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
OPENCODE_PROVIDER_ID = os.environ.get("OPENCODE_PROVIDER_ID")
OPENCODE_MODEL_ID = os.environ.get("OPENCODE_MODEL_ID")
PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8787"))
SESSION_PER_REQUEST = os.environ.get("SESSION_PER_REQUEST", "false").lower() == "true"

_shared_session_id: str | None = None


def _opencode_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{OPENCODE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if OPENCODE_SERVER_PASSWORD:
        token = base64.b64encode(
            f"{OPENCODE_SERVER_USERNAME}:{OPENCODE_SERVER_PASSWORD}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def _create_session() -> str:
    session = _opencode_request("POST", "/session", {})
    session_id = session.get("id")
    if not session_id:
        raise RuntimeError(f"opencode /session did not return an id: {session!r}")
    return session_id


def _send_message(session_id: str, message: str) -> str:
    body: dict = {"parts": [{"type": "text", "text": message}]}
    if OPENCODE_PROVIDER_ID:
        body["providerID"] = OPENCODE_PROVIDER_ID
    if OPENCODE_MODEL_ID:
        body["modelID"] = OPENCODE_MODEL_ID
    result = _opencode_request("POST", f"/session/{session_id}/message", body)
    parts = result.get("parts", [])
    text_chunks = [p.get("text", "") for p in parts if p.get("type") == "text"]
    return "\n".join(chunk for chunk in text_chunks if chunk)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        print(f"[proxy] {self.address_string()} - {fmt % args}")

    def do_POST(self) -> None:  # noqa: N802 — required BaseHTTPRequestHandler name
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
            message = payload.get("message", "")
            global _shared_session_id
            session_id = None if SESSION_PER_REQUEST else _shared_session_id
            if session_id is None:
                session_id = _create_session()
                if not SESSION_PER_REQUEST:
                    _shared_session_id = session_id
            reply = _send_message(session_id, message)
            body = json.dumps({"response": reply, "session_id": session_id}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self._send_error(e.code, f"opencode server error: {e.read().decode(errors='replace')}")
        except Exception as e:  # noqa: BLE001 — surfaced to caller as a 502
            self._send_error(502, str(e))

    def _send_error(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"Proxying POST /api/chat -> {OPENCODE_URL} (session-based opencode server API)")
    print(f"Listening on http://{PROXY_HOST}:{PROXY_PORT}")
    ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), Handler).serve_forever()
