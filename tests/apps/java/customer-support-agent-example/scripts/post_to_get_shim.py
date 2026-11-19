#!/usr/bin/env python3
"""Local POST-to-GET shim so NuGuard can chat with customer-support-agent-example.

NuGuard sends chat turns as HTTP POST with a JSON body. The example app only offers
GET /customerSupportAgent?sessionId=...&userMessage=... and answers in plain text.
This shim sits in front of the UNMODIFIED app and translates:

    POST /chat  {"userMessage": "...", "sessionId": "..."}
      -> GET  <upstream>/customerSupportAgent?sessionId=...&userMessage=...
      <- 200  {"response": "<plain text answer>"}

It binds to loopback only. Upstream errors keep their HTTP status.

Usage:
    python3 scripts/post_to_get_shim.py [--port 8088] [--upstream http://127.0.0.1:8087]
    python3 scripts/post_to_get_shim.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_PATH = "/customerSupportAgent"
DEFAULT_SESSION = "nuguard-default"
UPSTREAM_TIMEOUT = 120


def translate(body: dict, upstream: str) -> str:
    """Build the upstream GET URL from a POST body."""
    message = body.get("userMessage", body.get("message", ""))
    session = body.get("sessionId", body.get("session_id", "")) or DEFAULT_SESSION
    query = urllib.parse.urlencode({"sessionId": str(session), "userMessage": str(message)})
    return f"{upstream.rstrip('/')}{UPSTREAM_PATH}?{query}"


def make_handler(upstream: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            req = getattr(self, "_req_body", None)
            if req is not None:
                self._req_body = None
                print(
                    json.dumps(
                        {
                            "ts": time.strftime("%H:%M:%S"),
                            "session": str(req.get("sessionId", req.get("session_id", "")))[:40],
                            "user": str(req.get("userMessage", req.get("message", "")))[:600],
                            "status": code,
                            "response": str(payload.get("response", ""))[:600],
                        }
                    ),
                    flush=True,
                )
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_raw(self, code: int, data: bytes, content_type: str | None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type or "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            elif self.path.split("?")[0] == UPSTREAM_PATH:
                # Pass the real endpoint through unchanged, so NuGuard's liveness probe and its
                # direct-HTTP scenarios see the app itself instead of a 404 from the shim.
                try:
                    with urllib.request.urlopen(  # noqa: S310
                        upstream.rstrip("/") + self.path, timeout=UPSTREAM_TIMEOUT
                    ) as resp:
                        self._send_raw(resp.status, resp.read(), resp.headers.get("Content-Type"))
                except urllib.error.HTTPError as exc:
                    self._send_raw(exc.code, exc.read(), exc.headers.get("Content-Type"))
                except (urllib.error.URLError, TimeoutError, OSError):
                    self._send(502, {"response": "upstream unavailable"})
            else:
                self._send(404, {"response": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/chat":
                self._send(404, {"response": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
            except (ValueError, json.JSONDecodeError):
                self._send(400, {"response": "invalid JSON body"})
                return
            url = translate(body, upstream)
            self._req_body = body
            try:
                with urllib.request.urlopen(url, timeout=UPSTREAM_TIMEOUT) as resp:  # noqa: S310
                    text = resp.read().decode("utf-8", errors="replace")
                    self._send(resp.status, {"response": text})
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", errors="replace")[:1000]
                self._send(exc.code, {"response": text})
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self._send(502, {"response": f"upstream unavailable: {type(exc).__name__}"})

        def log_message(self, fmt: str, *args) -> None:  # keep logs quiet
            pass

    return Handler


def _self_test() -> int:
    seen: dict = {}

    class Fake(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            seen["path"] = parsed.path
            seen["query"] = urllib.parse.parse_qs(parsed.query)
            if seen["query"].get("userMessage", [""])[0] == "boom":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"upstream exploded")
                return
            data = "hello Ünïcode & more".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain;charset=UTF-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:
            pass

    fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    shim = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(f"http://127.0.0.1:{fake.server_port}")
    )
    for srv in (fake, shim):
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    def post(payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{shim.server_port}/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    code, out = post({"userMessage": "Cancel MS-777 & more? 100%", "sessionId": "s-1"})
    assert code == 200 and out == {"response": "hello Ünïcode & more"}, (code, out)
    assert seen["path"] == UPSTREAM_PATH, seen
    assert seen["query"] == {"sessionId": ["s-1"], "userMessage": ["Cancel MS-777 & more? 100%"]}, (
        seen
    )
    code, out = post({"message": "hi"})
    assert code == 200 and seen["query"]["sessionId"] == [DEFAULT_SESSION], seen
    code, out = post({"userMessage": "boom", "sessionId": "s-2"})
    assert code == 500 and "exploded" in out["response"], (code, out)
    with urllib.request.urlopen(  # noqa: S310
        f"http://127.0.0.1:{shim.server_port}{UPSTREAM_PATH}?sessionId=g-1&userMessage=hi%20there",
        timeout=10,
    ) as resp:
        assert resp.status == 200 and resp.read().decode() == "hello Ünïcode & more"
    assert seen["query"] == {"sessionId": ["g-1"], "userMessage": ["hi there"]}, seen
    shim.shutdown()
    fake.shutdown()
    dead = make_handler("http://127.0.0.1:1")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dead)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.server_port}/chat",
        data=b'{"userMessage":"x"}',
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
        raise AssertionError("expected 502")
    except urllib.error.HTTPError as exc:
        assert exc.code == 502, exc.code
    srv.shutdown()
    print("post_to_get_shim self-test OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--upstream", default="http://127.0.0.1:8087")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.upstream))
    print(f"shim listening on http://127.0.0.1:{args.port} -> {args.upstream}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
