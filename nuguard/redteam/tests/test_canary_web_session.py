"""Cookie-based second principal: canary tenants with ``web_session: true``."""
from __future__ import annotations

import asyncio
import itertools
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from nuguard.redteam.campaign.transport.principal import Principal
from nuguard.redteam.target.canary import CanaryConfig, CanaryTenant, acquire_web_session_tenants

_counter = itertools.count(1)


class _SessionHandler(BaseHTTPRequestHandler):
    """Issues a fresh session cookie and CSRF token per GET (like FinBot)."""

    def do_GET(self) -> None:  # noqa: N802
        n = next(_counter)
        body = f'<html><meta name="csrf-token" content="csrf-{n}"></html>'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", f"session=sess-{n}; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def _serve() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _SessionHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_web_session_tenants_get_independent_sessions() -> None:
    server = _serve()
    try:
        cfg = CanaryConfig(
            tenants=[
                CanaryTenant(tenant_id="a", web_session=True),
                CanaryTenant(tenant_id="b", web_session=True),
            ]
        )
        failed = asyncio.run(
            acquire_web_session_tenants(cfg, f"http://127.0.0.1:{server.server_port}")
        )
    finally:
        server.shutdown()
        server.server_close()
    assert failed == []
    a, b = (t.auth_headers() for t in cfg.tenants)
    assert "session=" in a["Cookie"] and a["Cookie"] != b["Cookie"]
    assert all(t.has_credentials for t in cfg.tenants)
    assert Principal.from_canary_tenant(cfg.tenants[0]).auth_scope != (
        Principal.from_canary_tenant(cfg.tenants[1]).auth_scope
    )


def test_credentials_never_serialised() -> None:
    tenant = CanaryTenant(tenant_id="a", session_headers={"Cookie": "session=secret"})
    assert "secret" not in tenant.model_dump_json()
    assert "secret" not in repr(tenant)


def test_unreachable_target_reports_failed_tenant() -> None:
    cfg = CanaryConfig(tenants=[CanaryTenant(tenant_id="a", web_session=True)])
    failed = asyncio.run(acquire_web_session_tenants(cfg, "http://127.0.0.1:1", timeout=1.0))
    assert failed == ["a"] and not cfg.tenants[0].has_credentials


def test_static_token_tenant_unchanged() -> None:
    tenant = CanaryTenant(tenant_id="a", session_token="tok")
    assert tenant.auth_headers() == {"Authorization": "Bearer tok"}
