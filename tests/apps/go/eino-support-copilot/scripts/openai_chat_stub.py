#!/usr/bin/env python3
"""Deterministic loopback OpenAI chat-completions stub for credential-free E2E runs.

Used by scripts/serve.sh when no Azure OpenAI key is set (fork PRs get no secrets). The agent
requires its reply to be one strict Draft JSON object (internal/domain.Draft), so every answer is a
Draft: a short support reply for account, payment or service questions, or a refusal for anything
else. It never calls tools.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_MODEL = "nuguard-e2e-stub"
_SUPPORT_WORDS = ("account", "subscription", "payment", "refund", "invoice", "billing", "service", "outage", "plan")


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


def _draft(user_message: str) -> dict:
    on_topic = any(word in user_message.lower() for word in _SUPPORT_WORDS)
    answer = (
        "I can help with your own account, payments and service status. "
        "Please share the account id you are asking about."
        if on_topic
        else "I'm sorry, I can only help with questions about your account, payments and service status."
    )
    return {
        "intent": "account_support" if on_topic else "out_of_scope",
        "priority": "P3",
        "answer": answer,
        "citations": [],
        "facts": [],
        "confidence": 0.5,
        "requires_review": False,
        "review_reasons": [],
        "follow_up_question": None,
    }


def _completion(payload: dict) -> dict:
    return {
        "id": "chatcmpl-nuguard-e2e",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(payload.get("model") or DEFAULT_MODEL),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(_draft(_last_user_message(payload)))},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: HTTP/1.0 closes per request and parallel clients see resets
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
            if not self.path.split("?", 1)[0].rstrip("/").endswith("/chat/completions"):
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
            f"http://127.0.0.1:{server.server_port}/openai/v1/chat/completions",
            data=json.dumps(
                {"model": DEFAULT_MODEL, "messages": [{"role": "user", "content": "Is my subscription active?"}]}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            payload = json.loads(response.read())
        assert response.status == 200
        draft = json.loads(payload["choices"][0]["message"]["content"])
        assert draft["intent"] == "account_support" and draft["priority"] == "P3"
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
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return
    server = ThreadingHTTPServer((args.host, args.port), make_handler())
    server.serve_forever()


if __name__ == "__main__":
    main()
