"""Unit tests for the W8 egress-callback canary server.

Covers both the generalized ``PoisonPayloadServer`` structured hit recording
and the ``CallbackCanaryServer`` typed-role facade, against the real asyncio
server (no mocking of the socket layer — it's a ~100-line hand-rolled HTTP
parser, worth exercising for real).
"""
from __future__ import annotations

import asyncio

import pytest

from nuguard.redteam.executor.poison_server import PoisonPayloadServer
from nuguard.redteam.target.callback_canary import CallbackCanaryServer


async def _raw_get(host: str, port: int, target: str, extra_headers: str = "") -> bytes:
    """Open a raw TCP connection and send a minimal HTTP/1.1 GET request."""
    reader, writer = await asyncio.open_connection(host, port)
    request = f"GET {target} HTTP/1.1\r\nHost: {host}\r\n{extra_headers}\r\n"
    writer.write(request.encode("utf-8"))
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    writer.close()
    return response


# ── PoisonPayloadServer structured hit recording ─────────────────────────────


@pytest.mark.asyncio
async def test_bare_trap_path_records_generic_role() -> None:
    async with PoisonPayloadServer() as server:
        await _raw_get(server.host, server.port, "/trap?foo=bar")
        records = server.trap_hit_records()
        assert len(records) == 1
        assert records[0].role == "generic"
        assert records[0].path == "/trap"
        assert records[0].query == "foo=bar"


@pytest.mark.asyncio
async def test_typed_role_paths_are_classified_correctly() -> None:
    async with PoisonPayloadServer() as server:
        await _raw_get(server.host, server.port, "/trap/ssrf")
        await _raw_get(server.host, server.port, "/trap/exfil/base64?data=xyz")
        await _raw_get(server.host, server.port, "/trap/beacon")
        roles = {r.role for r in server.trap_hit_records()}
        assert roles == {"ssrf", "exfil", "beacon"}


@pytest.mark.asyncio
async def test_non_trap_paths_are_not_recorded() -> None:
    async with PoisonPayloadServer() as server:
        await _raw_get(server.host, server.port, "/poison")
        await _raw_get(server.host, server.port, "/rag-poison")
        assert server.trap_hit_records() == []


@pytest.mark.asyncio
async def test_trap_hit_captures_headers_and_source_ip() -> None:
    async with PoisonPayloadServer() as server:
        await _raw_get(
            server.host, server.port, "/trap/ssrf",
            extra_headers="User-Agent: CipherBank-Agent/1.0\r\n",
        )
        [hit] = server.trap_hit_records()
        assert hit.headers.get("User-Agent") == "CipherBank-Agent/1.0"
        assert hit.source_ip  # loopback connection always has a peer IP


@pytest.mark.asyncio
async def test_trap_hit_records_since_filters_by_time_and_role() -> None:
    async with PoisonPayloadServer() as server:
        t0 = __import__("time").monotonic()
        await _raw_get(server.host, server.port, "/trap/ssrf")
        await _raw_get(server.host, server.port, "/trap/beacon")
        assert len(server.trap_hit_records_since(t0)) == 2
        assert len(server.trap_hit_records_since(t0, role="ssrf")) == 1
        assert len(server.trap_hit_records_since(t0, role="beacon")) == 1
        # A t0 in the future must exclude everything already recorded.
        future = t0 + 3600
        assert server.trap_hit_records_since(future) == []


# ── CallbackCanaryServer typed-role facade ───────────────────────────────────


@pytest.mark.asyncio
async def test_typed_role_urls_hit_the_right_paths() -> None:
    async with CallbackCanaryServer() as canary:
        assert canary.ssrf_proof_url().endswith("/trap/ssrf")
        assert canary.exfil_proof_url("base64").endswith("/trap/exfil/base64")
        assert canary.beacon_url().endswith("/trap/beacon")
        assert canary.netloc in canary.ssrf_proof_url()


@pytest.mark.asyncio
async def test_poll_for_hit_returns_immediately_when_hit_already_present() -> None:
    async with CallbackCanaryServer() as canary:
        t0 = __import__("time").monotonic()
        host, port = canary.host, canary.port
        await _raw_get(host, port, "/trap/ssrf")
        hit = await canary.poll_for_hit(t0, "ssrf", max_attempts=1)
        assert hit is not None
        assert hit.role == "ssrf"


@pytest.mark.asyncio
async def test_poll_for_hit_returns_none_when_no_hit_arrives() -> None:
    async with CallbackCanaryServer() as canary:
        t0 = __import__("time").monotonic()
        hit = await canary.poll_for_hit(t0, "ssrf", interval=0.01, max_attempts=2)
        assert hit is None


@pytest.mark.asyncio
async def test_poll_for_hit_picks_up_a_delayed_hit() -> None:
    async with CallbackCanaryServer() as canary:
        t0 = __import__("time").monotonic()
        host, port = canary.host, canary.port

        async def _delayed_hit() -> None:
            await asyncio.sleep(0.05)
            await _raw_get(host, port, "/trap/exfil/hex")

        task = asyncio.create_task(_delayed_hit())
        hit = await canary.poll_for_hit(t0, "exfil", interval=0.02, max_attempts=10)
        await task
        assert hit is not None
        assert hit.role == "exfil"


@pytest.mark.asyncio
async def test_poll_for_hit_ignores_a_different_role() -> None:
    async with CallbackCanaryServer() as canary:
        t0 = __import__("time").monotonic()
        await _raw_get(canary.host, canary.port, "/trap/beacon")
        hit = await canary.poll_for_hit(t0, "ssrf", interval=0.01, max_attempts=2)
        assert hit is None
