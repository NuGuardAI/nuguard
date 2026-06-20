"""Tests for AuthBootstrapper using respx HTTP mocks."""
from __future__ import annotations

import pytest
import respx
import httpx

from nuguard.common.auth import AuthConfig, LoginFlowConfig
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
async def test_default_credential_500_treated_as_ok() -> None:
    # HTTP 500 means the server is running but crashed on our minimal probe body
    # (e.g. a missing required field triggers a JS/Python TypeError).  The server
    # IS reachable so bootstrap should treat this as connectivity-ok, not
    # target_unavailable.  Actual scenario payloads use the full chat_payload_extras.
    respx.post(FULL_URL).mock(return_value=httpx.Response(500))
    report = await _bootstrapper().run()
    assert report.all_ok is True
    assert report.checks[0].status == "ok"
    assert report.checks[0].http_status_code == 500


@pytest.mark.anyio
@respx.mock
async def test_default_credential_target_unavailable_502() -> None:
    # HTTP 502 Bad Gateway means the upstream/proxy couldn't reach the server.
    respx.post(FULL_URL).mock(return_value=httpx.Response(502))
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


# ── login_flow fallback to direct chat-endpoint probe ───────────────────────


def _login_flow_auth() -> AuthConfig:
    return AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", token_response_key="token"),
    )


@pytest.mark.anyio
@respx.mock
async def test_login_flow_failure_falls_back_to_chat_endpoint_ok() -> None:
    # Login endpoint is broken, but the chat endpoint accepts the request anyway
    # (e.g. it needs no auth, or auth lives elsewhere) — bootstrap should use it.
    login_route = respx.post(f"{TARGET}/login").mock(return_value=httpx.Response(500, text="boom"))
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    bootstrapper = _bootstrapper(auth=_login_flow_auth())
    report = await bootstrapper.run()
    assert report.all_ok is True
    assert report.checks[0].status == "ok"
    # auth_type must reflect what was actually sent (none), not the
    # originally-configured login_flow that failed.
    assert report.checks[0].auth_type == "none"
    assert login_route.call_count == 1
    # A later 401 (e.g. from rate limiting) must not retry the already-dead
    # login endpoint — refresh_if_needed() should be a no-op for this session.
    refreshed = await bootstrapper.session.refresh_if_needed()
    assert refreshed is False
    assert login_route.call_count == 1


def _login_flow_auth_with_creds(username: str = "alice", password: str = "secret") -> AuthConfig:
    # Mirrors what resolve_auth_config_with_sbom_fallback produces when it
    # upgrades a basic-auth config to login_flow: the original username/password
    # are preserved on the upgraded config for this exact fallback.
    return AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", token_response_key="token"),
        username=username,
        password=password,
    )


@pytest.mark.anyio
@respx.mock
async def test_login_flow_failure_falls_back_to_basic_auth_with_original_creds() -> None:
    # auth.type was originally "basic" (username/password) and got upgraded to
    # login_flow via the SBOM's login endpoint. That endpoint is broken — bootstrap
    # should retry with the *original* username/password as HTTP Basic auth
    # straight to the chat endpoint, not an anonymous request.
    import base64

    respx.post(f"{TARGET}/login").mock(return_value=httpx.Response(500, text="boom"))
    chat_route = respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    bootstrapper = _bootstrapper(auth=_login_flow_auth_with_creds())
    report = await bootstrapper.run()

    expected = f"Basic {base64.b64encode(b'alice:secret').decode()}"
    assert report.all_ok is True
    assert chat_route.calls[0].request.headers["Authorization"] == expected
    # auth_type must reflect what was actually sent (basic), not the
    # originally-configured login_flow that failed.
    assert report.checks[0].auth_type == "basic"
    # session.headers() must return the working fallback credentials directly —
    # bootstrap swaps the session's auth config to type="basic" on success.
    assert bootstrapper.session.headers() == {"Authorization": expected}
    # And the dead login endpoint must not be retried again this session.
    refreshed = await bootstrapper.session.refresh_if_needed()
    assert refreshed is False


@pytest.mark.anyio
@respx.mock
async def test_login_flow_failure_and_chat_endpoint_failure_reports_both() -> None:
    respx.post(f"{TARGET}/login").mock(return_value=httpx.Response(500, text="boom"))
    respx.post(FULL_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    report = await _bootstrapper(auth=_login_flow_auth()).run()
    assert report.checks[0].status == "auth_failed"
    # The failing probe was sent with the fallback auth (none, since this
    # login_flow config has no username/password) — auth_type must say so,
    # not the originally-configured login_flow.
    assert report.checks[0].auth_type == "none"
    detail = report.checks[0].error_detail or ""
    assert "login endpoint" in detail
    assert "500" in detail
    assert "fallback probe to chat endpoint also failed" in detail


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
