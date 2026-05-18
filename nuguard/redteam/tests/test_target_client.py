"""Tests for TargetAppClient — circuit breaker and error-handling behaviour.

Key invariant: the client must return immediately on any response or error
(4xx, 5xx, network error) and NEVER wait for the full timeout before moving on.
Circuit-breaker state must only advance on 5xx and network errors, not on 4xx.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.redteam.target.client import (
    MAX_CONSECUTIVE_ERRORS,
    TargetAppClient,
    TargetUnavailableError,
)
from nuguard.redteam.target.session import AttackSession

# ── Helpers ───────────────────────────────────────────────────────────────────

BASE = "http://test-app"
CHAT = "/chat"


def _session() -> AttackSession:
    return AttackSession(session_id="s1", target_url=BASE, chain_id="c1")


async def _client() -> TargetAppClient:
    return TargetAppClient(base_url=BASE, chat_path=CHAT, timeout=5.0)


# ── 2xx — success, resets error counter ──────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_send_2xx_returns_immediately():
    """200 response is returned without waiting for the timeout."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "Hello"})
    )
    client = await _client()
    async with client:
        text, tool_calls = await client.send("hi", _session())
    assert text == "Hello"
    assert tool_calls == []
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
@respx.mock
async def test_send_form_payload_mode_uses_form_encoded_body():
    """Form payload mode should send message in request.form-style body."""
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    client = TargetAppClient(
        base_url=BASE,
        chat_path=CHAT,
        timeout=5.0,
        chat_payload_format="form",
    )
    async with client:
        text, _ = await client.send("hello", _session())
    assert text == "ok"
    assert route.called
    body = route.calls.last.request.content.decode()
    assert body == "message=hello"


@pytest.mark.asyncio
@respx.mock
async def test_send_2xx_resets_error_counter():
    """A successful 2xx response resets the circuit-breaker counter."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    client = await _client()
    client._consecutive_errors = 2  # pre-set as if two errors had occurred
    async with client:
        await client.send("hi", _session())
    assert client._consecutive_errors == 0


# ── 4xx — target alive, must NOT advance the circuit breaker ─────────────────


@pytest.mark.asyncio
@respx.mock
async def test_send_422_does_not_trip_circuit_breaker():
    """422 validation errors mean the target is up — circuit breaker must not fire."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(
            422,
            json={"detail": [{"msg": "field required", "loc": ["body", "message"]}]},
        )
    )
    client = await _client()
    async with client:
        for _ in range(MAX_CONSECUTIVE_ERRORS + 2):
            text, _ = await client.send("bad payload", _session())
            assert text == "[HTTP 422]"
    # Counter should stay at 0 throughout — target is alive and responding
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
@respx.mock
async def test_send_401_does_not_trip_circuit_breaker():
    """401 auth rejections mean the target is responding — no circuit-breaker penalty."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(401, json={"detail": "Unauthorized"})
    )
    client = await _client()
    async with client:
        for _ in range(MAX_CONSECUTIVE_ERRORS + 1):
            text, _ = await client.send("probe", _session())
            assert text == "[HTTP 401]"
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
@respx.mock
async def test_send_429_does_not_trip_circuit_breaker():
    """429 rate-limit means the target is alive — no circuit-breaker penalty."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(429, json={"detail": "Rate limited"})
    )
    client = await _client()
    async with client:
        for _ in range(MAX_CONSECUTIVE_ERRORS + 1):
            text, _ = await client.send("probe", _session())
            assert text == "[HTTP 429]"
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
@respx.mock
async def test_send_403_does_not_trip_circuit_breaker():
    """403 means the target is up and enforcing access control."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(403, json={"detail": "Forbidden"})
    )
    client = await _client()
    async with client:
        for _ in range(MAX_CONSECUTIVE_ERRORS + 1):
            text, _ = await client.send("probe", _session())
            assert text == "[HTTP 403]"
    assert client._consecutive_errors == 0


# ── 5xx — server error, DOES advance the circuit breaker ─────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_send_500_advances_circuit_breaker():
    """500 server errors count toward the circuit breaker."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
    )
    client = await _client()
    async with client:
        with pytest.raises(TargetUnavailableError):
            for _ in range(MAX_CONSECUTIVE_ERRORS + 1):
                await client.send("probe", _session())


@pytest.mark.asyncio
@respx.mock
async def test_send_503_trips_circuit_breaker():
    """503 service unavailable trips the circuit breaker after threshold."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    client = await _client()
    async with client:
        with pytest.raises(TargetUnavailableError):
            for _ in range(MAX_CONSECUTIVE_ERRORS + 1):
                await client.send("probe", _session())


# ── Network errors — DOES advance the circuit breaker ────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_send_network_error_advances_circuit_breaker():
    """Network-level failures (connection refused, DNS) trip the circuit breaker."""
    respx.post(f"{BASE}{CHAT}").mock(side_effect=httpx.ConnectError("refused"))
    client = await _client()
    async with client:
        with pytest.raises(TargetUnavailableError):
            for _ in range(MAX_CONSECUTIVE_ERRORS + 1):
                await client.send("probe", _session())


@pytest.mark.asyncio
@respx.mock
async def test_send_network_error_returns_error_string():
    """Network errors return immediately as [REQUEST_ERROR: ...] — no timeout wait."""
    respx.post(f"{BASE}{CHAT}").mock(side_effect=httpx.ConnectError("refused"))
    client = await _client()
    # Only trigger once so circuit breaker doesn't fire
    async with client:
        text, _ = await client.send("probe", _session())
    assert text.startswith("[REQUEST_ERROR:")


# ── Mixed: 4xx then recovery ──────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_4xx_followed_by_200_not_counted():
    """4xx responses do not accumulate — a 200 after several 4xx stays clean."""
    route = respx.post(f"{BASE}{CHAT}")
    route.side_effect = [
        httpx.Response(422, json={"detail": "bad format"}),
        httpx.Response(422, json={"detail": "bad format"}),
        httpx.Response(200, json={"response": "success"}),
    ]
    client = await _client()
    async with client:
        r1, _ = await client.send("bad1", _session())
        r2, _ = await client.send("bad2", _session())
        r3, _ = await client.send("good", _session())
    assert r1 == "[HTTP 422]"
    assert r2 == "[HTTP 422]"
    assert r3 == "success"
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
@respx.mock
async def test_5xx_then_4xx_does_not_reset_5xx_count():
    """5xx errors accumulate; a 4xx response resets the counter (target alive)."""
    route = respx.post(f"{BASE}{CHAT}")
    route.side_effect = [
        httpx.Response(500, json={}),      # error count: 1
        httpx.Response(422, json={}),      # 4xx: count resets to 0
        httpx.Response(500, json={}),      # error count: 1
    ]
    client = await _client()
    async with client:
        await client.send("probe1", _session())
        assert client._consecutive_errors == 1
        await client.send("probe2", _session())
        assert client._consecutive_errors == 0  # 4xx reset it
        await client.send("probe3", _session())
        assert client._consecutive_errors == 1


# ── Circuit-breaker threshold ─────────────────────────────────────────────────


def test_default_max_consecutive_errors():
    """Threshold is 3 by default."""
    assert MAX_CONSECUTIVE_ERRORS == 3


@pytest.mark.asyncio
@respx.mock
async def test_circuit_breaker_fires_exactly_at_threshold():
    """TargetUnavailableError is raised when the threshold is hit, not before."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(500, json={})
    )
    client = await _client()
    async with client:
        # First MAX_CONSECUTIVE_ERRORS - 1 errors: no exception yet
        for i in range(MAX_CONSECUTIVE_ERRORS - 1):
            text, _ = await client.send("probe", _session())
            assert text == "[HTTP 500]"
        # The Nth error triggers TargetUnavailableError
        with pytest.raises(TargetUnavailableError):
            await client.send("probe", _session())


# ── 404 — resource not found, target is alive (4xx) ──────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_send_404_does_not_trip_circuit_breaker():
    """404 not-found is a 4xx: target is up, circuit breaker must not advance."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(404, json={"detail": "Not Found"})
    )
    client = await _client()
    async with client:
        for _ in range(MAX_CONSECUTIVE_ERRORS + 2):
            text, _ = await client.send("probe", _session())
            assert text == "[HTTP 404]"
    assert client._consecutive_errors == 0


# ── 504 — gateway timeout, server-side error (5xx) ───────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_send_504_trips_circuit_breaker():
    """504 gateway timeout is a 5xx: counts toward the circuit breaker."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(504, text="Gateway Timeout")
    )
    client = await _client()
    async with client:
        with pytest.raises(TargetUnavailableError):
            for _ in range(MAX_CONSECUTIVE_ERRORS + 1):
                await client.send("probe", _session())


# ── Default headers — auth propagation ───────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_default_headers_sent_with_send():
    """Auth header supplied at construction is included in every send() request."""
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    client = TargetAppClient(
        base_url=BASE,
        chat_path=CHAT,
        timeout=5.0,
        default_headers={"Authorization": "Bearer test-token"},
    )
    async with client:
        text, _ = await client.send("hello", _session())
    assert text == "ok"
    sent_request = route.calls[0].request
    assert sent_request.headers.get("authorization") == "Bearer test-token"


@pytest.mark.asyncio
@respx.mock
async def test_default_headers_sent_with_invoke_endpoint():
    """Auth header is included in invoke_endpoint() direct HTTP requests."""
    route = respx.post(f"{BASE}/api/resource").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = TargetAppClient(
        base_url=BASE,
        chat_path=CHAT,
        timeout=5.0,
        default_headers={"Authorization": "Bearer test-token"},
    )
    async with client:
        status, _text, _json = await client.invoke_endpoint("/api/resource", method="POST")
    assert status == 200
    sent_request = route.calls[0].request
    assert sent_request.headers.get("authorization") == "Bearer test-token"


@pytest.mark.asyncio
@respx.mock
async def test_no_default_headers_backward_compatible():
    """When no default_headers are supplied the client still works as before."""
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "hello"})
    )
    client = TargetAppClient(base_url=BASE, chat_path=CHAT, timeout=5.0)
    async with client:
        text, _ = await client.send("hi", _session())
    assert text == "hello"
    assert client._consecutive_errors == 0
