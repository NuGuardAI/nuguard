"""Tests for BranchTransport + TargetAppClient transport hooks (campaign increment 1)."""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.redteam.campaign.transport import BranchTransport, RetryDeferred
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession

BASE = "http://test-app"
CHAT = "/chat"


def _session(sid: str = "s1") -> AttackSession:
    return AttackSession(session_id=sid, target_url=BASE, chain_id="c1")


def _client(**kw) -> TargetAppClient:
    return TargetAppClient(base_url=BASE, chat_path=CHAT, timeout=5.0, **kw)


@pytest.mark.asyncio
@respx.mock
async def test_branches_do_not_share_conversation_ids() -> None:
    """Interleaved branches each forward only their own server-issued ids."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        seen.append(body)
        n = len(seen)
        return httpx.Response(200, json={"response": "ok", "conversation_id": f"conv-{n}"})

    respx.post(f"{BASE}{CHAT}").mock(side_effect=handler)
    a, b = BranchTransport("A"), BranchTransport("B")
    async with _client() as client:
        await client.send("a1", _session(), transport=a)   # → conv-1 stored on A
        await client.send("b1", _session(), transport=b)   # → conv-2 stored on B
        await client.send("a2", _session(), transport=a)   # must carry conv-1
        await client.send("b2", _session(), transport=b)   # must carry conv-2
    assert "conversation_id" not in seen[0] and "conversation_id" not in seen[1]
    assert seen[2]["conversation_id"] == "conv-1"
    assert seen[3]["conversation_id"] == "conv-2"
    assert client._session_context == {}  # client-wide state never touched


@pytest.mark.asyncio
@respx.mock
async def test_branch_headers_and_cookies_isolated() -> None:
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(
            200, json={"response": "ok"}, headers={"set-cookie": "sid=abc; Path=/"}
        )
    )
    a = BranchTransport("A", headers={"Authorization": "Bearer A"})
    b = BranchTransport("B", headers={"Authorization": "Bearer B"})
    async with _client() as client:
        await client.send("hi", _session(), transport=a)
        await client.send("hi", _session(), transport=b)
        await client.send("hi", _session(), transport=a)
    reqs = [c.request for c in route.calls]
    assert reqs[0].headers["authorization"] == "Bearer A"
    assert reqs[1].headers["authorization"] == "Bearer B"
    assert a.cookies == {"sid": "abc"}
    # B's request carried an explicit (empty) Cookie header: A's cookie never leaks.
    assert reqs[1].headers.get("cookie", "") == ""
    assert reqs[2].headers["cookie"] == "sid=abc"


@pytest.mark.asyncio
@respx.mock
async def test_429_defers_instead_of_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(429, headers={"retry-after": "7"}, json={})
    )
    slept: list[float] = []

    async def fake_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr("nuguard.redteam.target.client.asyncio.sleep", fake_sleep)
    async with _client() as client:
        with pytest.raises(RetryDeferred) as ei:
            await client.send("hi", _session(), transport=BranchTransport("A"))
    assert ei.value.delay_seconds > 0
    assert slept == []  # the request slot is released; no in-call sleep


@pytest.mark.asyncio
@respx.mock
async def test_legacy_path_unchanged_without_transport() -> None:
    respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "hi", "session_id": "S"})
    )
    async with _client() as client:
        await client.send("x", _session())
    assert client._session_context == {"session_id": "S"}


@pytest.mark.asyncio
@respx.mock
async def test_invoke_endpoint_uses_branch_headers_and_strip_auth() -> None:
    route = respx.get(f"{BASE}/api/me").mock(return_value=httpx.Response(200, json={"id": 1}))
    a = BranchTransport("A", headers={"Authorization": "Bearer A"})
    async with _client() as client:
        await client.invoke_endpoint("/api/me", method="GET", transport=a)
        await client.invoke_endpoint("/api/me", method="GET", transport=a, strip_auth=True)
    assert route.calls[0].request.headers["authorization"] == "Bearer A"
    assert "authorization" not in route.calls[1].request.headers


@pytest.mark.asyncio
@respx.mock
async def test_invoke_endpoint_429_defers() -> None:
    respx.get(f"{BASE}/api/x").mock(return_value=httpx.Response(429, json={}))
    async with _client() as client:
        with pytest.raises(RetryDeferred):
            await client.invoke_endpoint("/api/x", method="GET", transport=BranchTransport("A"))


def test_request_headers_always_emits_explicit_cookie() -> None:
    assert BranchTransport("A").request_headers()["Cookie"] == ""
    assert BranchTransport("A", cookies={"k": "v"}).request_headers({"X": "1"}) == {
        "Cookie": "k=v",
        "X": "1",
    }


def test_principal_fingerprint_hides_credentials() -> None:
    from nuguard.redteam.campaign.transport import Principal

    p = Principal.from_headers("primary", {"Authorization": "Bearer SECRET-TOKEN"})
    assert "SECRET-TOKEN" not in repr(p) and "SECRET-TOKEN" not in p.auth_scope
    assert p.auth_scope != Principal.from_headers("x", {"Authorization": "Bearer other"}).auth_scope
    assert Principal.anonymous().auth_scope == "anonymous"


def test_branch_rejects_mixed_principals() -> None:
    from nuguard.redteam.campaign.transport import Principal

    t = BranchTransport("A")
    a = Principal.from_headers("a", {"Authorization": "Bearer A"})
    b = Principal.from_headers("b", {"Authorization": "Bearer B"})
    t.pin_principal(a)
    t.pin_principal(a)  # idempotent
    with pytest.raises(ValueError, match="refusing to mix"):
        t.pin_principal(b)
    assert t.headers["Authorization"] == "Bearer A"


@pytest.mark.asyncio
@respx.mock
async def test_deferred_retry_releases_limiter_slot() -> None:
    from nuguard.redteam.campaign.transport import BranchSender, TargetLimiter

    respx.post(f"{BASE}{CHAT}").mock(return_value=httpx.Response(429, json={}))
    limiter = TargetLimiter(max_concurrent_requests=1)
    async with _client() as client:
        sender = BranchSender(client, BranchTransport("A"), limiter)
        with pytest.raises(RetryDeferred):
            await sender.send("hi", _session())
        # Slot was released by the deferral: a second acquire must not block.
        import asyncio

        await asyncio.wait_for(limiter.__aenter__(), timeout=1.0)
        await limiter.__aexit__(None, None, None)
