"""A refreshed web session must clear the chat route, not just carry some CSRF token."""
from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from nuguard.common.web_session import make_session_refresher

_PAGES = {
    # public landing page: a site-wide token the portal rejects, plus a link to the portal
    "/": ("pub", "s-public", '<a href="/admin/">admin</a>'),
    "/admin/": ("adm", "s-admin", ""),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        page = _PAGES.get(self.path.split("?")[0])
        if page is None:
            self.send_response(404)
            self.end_headers()
            return
        token, cookie, extra = page
        body = f'<html><meta name="csrf-token" content="{token}">{extra}</html>'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", f"session={cookie}; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        ok = (
            self.headers.get("X-CSRF-Token") == "adm"
            and "session=s-admin" in (self.headers.get("Cookie") or "")
        )
        body = b'{"detail":"validation error"}' if ok else (
            b'{"error":{"code":403,"message":"CSRF token validation failed","details":"CSRF token mismatch"}}'
        )
        self.send_response(422 if ok else 403)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def _refresh(validate_paths: list[str] | None) -> dict[str, str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        refresher = make_session_refresher(
            f"http://127.0.0.1:{server.server_port}", [""], validate_paths=validate_paths
        )
        async def _call() -> dict[str, str]:
            return await refresher()

        return asyncio.run(_call())
    finally:
        server.shutdown()
        server.server_close()


def test_unvalidated_refresh_takes_the_public_token_and_is_rejected_again() -> None:
    headers = _refresh(None)
    assert headers.get("X-CSRF-Token") == "pub"  # the failure mode: wrong, site-wide token


def test_validated_refresh_finds_the_portal_session() -> None:
    headers = _refresh(["/admin/chat"])
    assert headers.get("X-CSRF-Token") == "adm"
    assert "session=s-admin" in headers["Cookie"]
