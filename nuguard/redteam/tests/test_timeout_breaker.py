"""Scenario timeouts must not trip the run-level breaker while the target is alive."""
from __future__ import annotations

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from nuguard.redteam.executor.orchestrator import _target_is_responsive


class _NotFoundHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return


def test_target_with_404_is_responsive() -> None:
    """Any HTTP answer (even 404, no /health route) counts as alive."""
    server = HTTPServer(("127.0.0.1", 0), _NotFoundHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        assert asyncio.run(_target_is_responsive(url, timeout=3.0)) is True
    finally:
        server.shutdown()
        server.server_close()


def test_closed_port_is_unresponsive() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert asyncio.run(_target_is_responsive(f"http://127.0.0.1:{port}", timeout=2.0)) is False
