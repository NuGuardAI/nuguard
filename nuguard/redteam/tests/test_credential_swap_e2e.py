"""End-to-end verification for issue #561's credential-swap fix.

Section I's unit tests prove that ``apply_secondary_credential`` sets the
right ``strip_auth``/``extra_headers`` fields on an ``ExploitStep``, and that
``ScenarioGenerator`` wires those fields in from a configured canary tenant.
Neither proves those fields actually change what goes out on the wire — that
depends on ``AttackExecutor`` correctly forwarding them into
``TargetAppClient.invoke_endpoint()``, which is real, unmocked production
code here. This module closes that gap: a real ``TargetAppClient`` against a
``respx``-mocked HTTP transport, driven by a real ``AttackExecutor``, running
an actual ``build_mass_assignment`` chain (with and without the canary swap
applied), asserting on the literal ``Authorization`` header respx intercepts.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.redteam.executor.executor import AttackExecutor
from nuguard.redteam.scenarios._chain_factory import apply_secondary_credential
from nuguard.redteam.scenarios.api_attacks import build_mass_assignment
from nuguard.redteam.target.client import TargetAppClient

BASE = "http://target-app.test"
CHAT = "/chat"
PRIMARY_TOKEN = "Bearer primary-run-token"
CANARY_TOKEN = "Bearer canary-tenant-token"


def _client() -> TargetAppClient:
    return TargetAppClient(
        base_url=BASE,
        chat_path=CHAT,
        timeout=5.0,
        default_headers={"Authorization": PRIMARY_TOKEN},
    )


@pytest.mark.asyncio
@respx.mock
async def test_mass_assignment_uses_canary_credential_on_the_wire_when_configured():
    """The literal HTTP request AttackExecutor sends for a mass-assignment
    step carries the canary tenant's Authorization header, not the run's own
    primary one, once apply_secondary_credential has swapped the step."""
    route = respx.post(f"{BASE}/api/users").mock(
        return_value=httpx.Response(200, json={"id": 1, "created": True})
    )
    scenario = build_mass_assignment("ep1", "Create User", "/api/users", method="POST")
    apply_secondary_credential(scenario, {"Authorization": CANARY_TOKEN})

    client = _client()
    executor = AttackExecutor(client=client)
    async with client:
        await executor.run(scenario.chain)

    assert route.called
    sent_request = route.calls[0].request
    assert sent_request.headers.get("authorization") == CANARY_TOKEN


@pytest.mark.asyncio
@respx.mock
async def test_mass_assignment_uses_primary_credential_on_the_wire_without_canary():
    """No canary configured (apply_secondary_credential never called, mirrors
    ScenarioGenerator's fallback path) — the request still carries the run's
    own primary credential, exactly as before this fix, since falling back to
    primary-credential behaviour (not silently failing to attack at all) is
    the documented degradation."""
    route = respx.post(f"{BASE}/api/users").mock(
        return_value=httpx.Response(200, json={"id": 1, "created": True})
    )
    scenario = build_mass_assignment("ep1", "Create User", "/api/users", method="POST")

    client = _client()
    executor = AttackExecutor(client=client)
    async with client:
        await executor.run(scenario.chain)

    assert route.called
    sent_request = route.calls[0].request
    assert sent_request.headers.get("authorization") == PRIMARY_TOKEN


@pytest.mark.asyncio
@respx.mock
async def test_price_tampering_uses_canary_credential_on_the_wire_when_configured():
    """Second write-capable builder, to confirm the wire-level proof isn't
    specific to mass assignment's particular step shape."""
    from nuguard.redteam.scenarios.api_attacks import build_price_tampering

    route = respx.post(f"{BASE}/checkout").mock(
        return_value=httpx.Response(200, json={"price": 0.01, "ok": True})
    )
    scenario = build_price_tampering("ep2", "Checkout", "/checkout", method="POST")
    apply_secondary_credential(scenario, {"Authorization": CANARY_TOKEN})

    client = _client()
    executor = AttackExecutor(client=client)
    async with client:
        await executor.run(scenario.chain)

    assert route.called
    sent_request = route.calls[0].request
    assert sent_request.headers.get("authorization") == CANARY_TOKEN
