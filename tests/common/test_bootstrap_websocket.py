"""Tests for AuthBootstrapper's WebSocket handshake health-check path.

Companion to test_bootstrap.py (HTTP). Spins up a real local `websockets`
server so tests exercise the actual handshake instead of mocking the library.
"""
from __future__ import annotations

import pytest
from websockets.asyncio.server import serve

from nuguard.common.auth import AuthConfig
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.common.errors import TargetUnavailableError


def _ws_bootstrapper(port: int, **kwargs) -> AuthBootstrapper:
    return AuthBootstrapper(
        target_url=f"http://localhost:{port}",
        endpoint="/ws",
        default_auth=AuthConfig(type="none"),
        run_id="test-run",
        startup_retries=0,
        is_websocket=True,
        **kwargs,
    )


async def _accept_handler(ws) -> None:
    async for _ in ws:
        pass


async def _reject_handler(connection, request):
    # Reject the handshake itself (process_request runs before accept).
    return connection.respond(401, "Unauthorized")


@pytest.mark.anyio
async def test_websocket_bootstrap_ok_on_successful_handshake() -> None:
    async with serve(_accept_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        report = await _ws_bootstrapper(port).run()
    assert report.all_ok is True
    assert report.checks[0].status == "ok"


@pytest.mark.anyio
async def test_websocket_bootstrap_auth_failed_on_401_handshake_rejection() -> None:
    async with serve(_accept_handler, "localhost", 0, process_request=_reject_handler) as server:
        port = server.sockets[0].getsockname()[1]
        report = await _ws_bootstrapper(port).run()
    assert report.all_ok is False
    assert report.checks[0].status == "auth_failed"
    assert report.checks[0].http_status_code == 401


@pytest.mark.anyio
async def test_websocket_bootstrap_target_unavailable_when_nothing_listening() -> None:
    bootstrapper = _ws_bootstrapper(1)  # nothing listening on port 1
    with pytest.raises(TargetUnavailableError):
        await bootstrapper.run()


@pytest.mark.anyio
async def test_websocket_bootstrap_sends_first_message_auth() -> None:
    received: list[str] = []

    async def _auth_handler(ws) -> None:
        received.append(await ws.recv())

    async with serve(_auth_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        report = await _ws_bootstrapper(
            port, ws_auth_message={"type": "auth", "token": "secret"}
        ).run()

    assert report.all_ok is True
    assert received == ['{"type": "auth", "token": "secret"}']
