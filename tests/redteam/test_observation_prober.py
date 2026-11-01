"""Tests for the W7 passive observation-channel tap (catalog L-series)."""
from __future__ import annotations

import asyncio

import pytest
import websockets.asyncio.server

from nuguard.redteam.enrichment.asm_models import AsmObservationChannel
from nuguard.redteam.enrichment.observation_prober import (
    build_observation_findings,
    run_observation_pass,
)
from nuguard.redteam.target.ws_client import connect_and_listen


async def _start_broadcasting_server(messages: list[str], delay_s: float = 0.0):
    async def handler(connection: object) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        for msg in messages:
            await connection.send(msg)  # type: ignore[attr-defined]

    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return server, f"ws://127.0.0.1:{port}"


# ── connect_and_listen ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_and_listen_collects_broadcast_messages() -> None:
    server, url = await _start_broadcasting_server(["hello", "world"])
    try:
        events = await connect_and_listen(url, duration_s=0.3)
    finally:
        server.close()
        await server.wait_closed()

    assert events == ["hello", "world"]


@pytest.mark.asyncio
async def test_connect_and_listen_respects_max_events() -> None:
    server, url = await _start_broadcasting_server([f"msg-{i}" for i in range(10)])
    try:
        events = await connect_and_listen(url, duration_s=0.3, max_events=3)
    finally:
        server.close()
        await server.wait_closed()

    assert len(events) == 3


@pytest.mark.asyncio
async def test_connect_and_listen_returns_empty_on_connection_failure() -> None:
    events = await connect_and_listen("ws://127.0.0.1:1", duration_s=0.2, open_timeout=0.5)
    assert events == []


@pytest.mark.asyncio
async def test_connect_and_listen_never_sends_anything() -> None:
    """The handler never receives a message — proving the tap is passive."""
    received: list[str] = []

    async def handler(connection: object) -> None:
        try:
            async for message in connection:  # type: ignore[attr-defined]
                received.append(message)
        except Exception:  # noqa: BLE001
            pass

    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        await connect_and_listen(f"ws://127.0.0.1:{port}", duration_s=0.3)
    finally:
        server.close()
        await server.wait_closed()

    assert received == []


# ── run_observation_pass ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_observation_pass_skips_auth_required_channels() -> None:
    channel = AsmObservationChannel(url="ws://127.0.0.1:1", transport="ws", connect_auth_required=True)
    results = await run_observation_pass([channel])
    assert results == {}


@pytest.mark.asyncio
async def test_run_observation_pass_collects_from_reachable_channel() -> None:
    server, url = await _start_broadcasting_server(['{"user_id": "ACC2002", "name": "Bob"}'])
    try:
        channel = AsmObservationChannel(url=url, transport="ws", connect_auth_required=False)
        results = await run_observation_pass([channel], duration_s=0.3)
    finally:
        server.close()
        await server.wait_closed()

    assert url in results
    assert results[url]


# ── build_observation_findings ───────────────────────────────────────────────


def test_build_observation_findings_flags_identifier_in_broadcast() -> None:
    events_by_channel = {"ws://x/logs": ['{"account_id": "ACC2002", "email": "bob@example.com"}']}
    findings = build_observation_findings(events_by_channel)

    assert len(findings) == 1
    assert "ws://x/logs" in findings[0].title


def test_build_observation_findings_empty_when_no_identifiers() -> None:
    events_by_channel = {"ws://x/logs": ["heartbeat", "ping", "ok"]}
    findings = build_observation_findings(events_by_channel)
    assert findings == []
