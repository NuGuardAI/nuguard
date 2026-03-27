"""Tests for AuthBootstrapper using respx HTTP mocks."""
from __future__ import annotations

import pytest
import respx
import httpx

from nuguard.common.auth import AuthConfig
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.common.errors import TargetUnavailableError
from nuguard.redteam.target.canary import CanaryConfig, CanaryTenant

TARGET = "http://target.test"
ENDPOINT = "/chat"
FULL_URL = f"{TARGET}{ENDPOINT}"


def _bootstrapper(
    auth: AuthConfig | None = None,
    canary: CanaryConfig | None = None,
) -> AuthBootstrapper:
    return AuthBootstrapper(
        target_url=TARGET,
        endpoint=ENDPOINT,
        default_auth=auth or AuthConfig(type="none"),
        canary_config=canary,
        run_id="test-run",
    )


def _canary_with_tenants(*tokens: str) -> CanaryConfig:
    tenants = [
        CanaryTenant(tenant_id=f"t{i}", session_token=tok)
        for i, tok in enumerate(tokens)
    ]
    return CanaryConfig(tenants=tenants)


# ── default credential tests ────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_default_credential_ok() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    report = await _bootstrapper().run()
    assert report.all_ok is True
    assert len(report.checks) == 1
    assert report.checks[0].status == "ok"
    assert report.checks[0].identity == "default"


@pytest.mark.anyio
@respx.mock
async def test_default_credential_auth_failed_401() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    report = await _bootstrapper().run()
    assert report.all_ok is False
    assert report.checks[0].status == "auth_failed"
    assert report.checks[0].http_status_code == 401


@pytest.mark.anyio
@respx.mock
async def test_default_credential_auth_failed_403() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(403))
    report = await _bootstrapper().run()
    assert report.checks[0].status == "auth_failed"
    assert report.checks[0].http_status_code == 403


@pytest.mark.anyio
@respx.mock
async def test_default_credential_target_unavailable_500() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(TargetUnavailableError):
        await _bootstrapper().run()


@pytest.mark.anyio
@respx.mock
async def test_default_credential_network_error() -> None:
    respx.post(FULL_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TargetUnavailableError):
        await _bootstrapper().run()


@pytest.mark.anyio
@respx.mock
async def test_default_credential_timeout() -> None:
    respx.post(FULL_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    with pytest.raises(TargetUnavailableError):
        await _bootstrapper().run()


# ── tenant token tests ───────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_tenant_token_ok() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    canary = _canary_with_tenants("tok-tenant-1")
    report = await _bootstrapper(canary=canary).run()
    assert report.all_ok is True
    assert len(report.checks) == 2  # default + 1 tenant
    assert all(c.status == "ok" for c in report.checks)


@pytest.mark.anyio
@respx.mock
async def test_tenant_token_skipped_when_empty() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    canary = _canary_with_tenants("")  # empty token
    report = await _bootstrapper(canary=canary).run()
    skipped = [c for c in report.checks if c.status == "skipped"]
    assert len(skipped) == 1
    assert report.all_ok is True  # skipped doesn't count as failure


@pytest.mark.anyio
@respx.mock
async def test_tenant_token_auth_failed() -> None:
    # default 200, tenant 401
    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200) if call_count == 1 else httpx.Response(401)

    respx.post(FULL_URL).mock(side_effect=side_effect)
    canary = _canary_with_tenants("tok-tenant-bad")
    report = await _bootstrapper(canary=canary).run()
    assert report.all_ok is False
    tenant_check = report.checks[1]
    assert tenant_check.status == "auth_failed"


@pytest.mark.anyio
@respx.mock
async def test_multi_tenant_partial_failure() -> None:
    responses = [httpx.Response(200), httpx.Response(200), httpx.Response(401)]
    respx.post(FULL_URL).side_effect = responses
    canary = _canary_with_tenants("tok-t1", "tok-t2")
    report = await _bootstrapper(canary=canary).run()
    assert report.all_ok is False
    assert len(report.failed_checks) == 1


# ── header injection tests ───────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_headers_injected_correctly_bearer() -> None:
    route = respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    auth = AuthConfig(type="bearer", header="Authorization: Bearer mytoken")
    await _bootstrapper(auth=auth).run()
    sent = route.calls[0].request
    assert sent.headers["Authorization"] == "Bearer mytoken"


@pytest.mark.anyio
@respx.mock
async def test_headers_injected_correctly_none() -> None:
    route = respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    await _bootstrapper(auth=AuthConfig(type="none")).run()
    sent = route.calls[0].request
    assert "authorization" not in {k.lower() for k in sent.headers.keys()}
