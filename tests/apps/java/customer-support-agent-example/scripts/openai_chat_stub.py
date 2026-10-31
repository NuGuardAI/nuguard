#!/usr/bin/env python3
"""Deterministic loopback OpenAI chat-completions stub for credential-free E2E runs."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_MODEL = "nuguard-e2e-stub"


def _last_user_message(payload: dict) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return ""


def _completion(payload: dict) -> dict:
    user_message = _last_user_message(payload)
    if "booking" in user_message.lower():
        answer = (
            "I can help with a Miles of Smiles booking. Please provide the booking number "
            "and the customer's first and last name."
        )
    else:
        answer = "I'm sorry, I can only help with Miles of Smiles car-rental questions."
    return {
        "id": "chatcmpl-nuguard-e2e",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(payload.get("model") or DEFAULT_MODEL),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send_json(200, {"status": "ok"})
            else:
                self._send_json(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") not in {"/chat/completions", "/v1/chat/completions"}:
                self._send_json(404, {"error": {"message": "not found"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("request must be a JSON object")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": {"message": str(exc)}})
                return
            self._send_json(200, _completion(payload))

        def log_message(self, fmt: str, *args) -> None:
            pass

    return Handler


def _self_test() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": DEFAULT_MODEL,
                    "messages": [{"role": "user", "content": "Cancel my booking"}],
                    "tools": [],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            payload = json.loads(response.read())
        assert response.status == 200
        assert payload["choices"][0]["message"]["content"]
        with urllib.request.urlopen(  # noqa: S310
            f"http://127.0.0.1:{server.server_port}/health", timeout=5
        ) as response:
            assert json.loads(response.read()) == {"status": "ok"}
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return
    server = ThreadingHTTPServer((args.host, args.port), make_handler())
    server.serve_forever()


if __name__ == "__main__":
    main()
