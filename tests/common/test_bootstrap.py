"""Tests for AuthBootstrapper using respx HTTP mocks."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.bootstrap import BOOTSTRAP_STARTUP_RETRIES, AuthBootstrapper
from nuguard.common.errors import TargetUnavailableError
from nuguard.redteam.target.canary import CanaryConfig, CanaryTenant

TARGET = "http://target.test"
ENDPOINT = "/chat"
FULL_URL = f"{TARGET}{ENDPOINT}"


def _bootstrapper(
    auth: AuthConfig | None = None,
    canary: CanaryConfig | None = None,
    startup_retries: int = 0,
) -> AuthBootstrapper:
    return AuthBootstrapper(
        target_url=TARGET,
        endpoint=ENDPOINT,
        default_auth=auth or AuthConfig(type="none"),
        canary_config=canary,
        run_id="test-run",
        startup_retries=startup_retries,
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



# ── probe body construction ─────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_probe_body_flat_extras_merges_message() -> None:
    # Flat (depth <= 1) chat_payload_extras: unchanged legacy behavior — extras
    # are merged as sibling fields alongside the flat "message": "ping" probe.
    route = respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    bootstrapper = AuthBootstrapper(
        target_url=TARGET,
        endpoint=ENDPOINT,
        default_auth=AuthConfig(type="none"),
        run_id="test-run",
        startup_retries=0,
        probe_payload_extras={"vehicleState": "parked"},
    )
    await bootstrapper.run()
    sent_body = route.calls.last.request.content
    assert json.loads(sent_body) == {"vehicleState": "parked", "message": "ping"}


@pytest.mark.anyio
@respx.mock
async def test_probe_body_nested_extras_substituted() -> None:
    # Nested (slot-mode) chat_payload_extras must be substituted, not clobbered
    # by a flat "message": "ping" overlay — see nuguard.yaml's chat_payload_extras
    # slot-mode docs and TargetAppClient._build_generic_body.
    route = respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    bootstrapper = AuthBootstrapper(
        target_url=TARGET,
        endpoint=ENDPOINT,
        default_auth=AuthConfig(type="none"),
        run_id="test-run",
        startup_retries=0,
        probe_payload_extras={
            "message": {"role": "user", "content": "{{message}}"},
            "conversation_id": "{{conversation_id}}",
            "stream": False,
        },
    )
    await bootstrapper.run()
    sent_body = route.calls.last.request.content
    assert json.loads(sent_body) == {
        "message": {"role": "user", "content": "ping"},
        "stream": False,
    }


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
    # The login response body must never appear in user-visible error_detail —
    # only status codes/keys, never raw response content (may hold secrets/PII).
    assert "boom" not in detail


@pytest.mark.anyio
@respx.mock
async def test_login_failure_reasons_do_not_leak_response_body() -> None:
    # Each of these failure modes used to embed a truncated response body
    # (resp.text / str(body)) into login_error, which gets surfaced to users
    # via CredentialCheckResult.error_detail. login_error must stay body-free.
    respx.post(f"{TARGET}/login").mock(
        return_value=httpx.Response(500, text="super-secret-internal-detail")
    )
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    bootstrapper = _bootstrapper(auth=_login_flow_auth())
    await bootstrapper.run()
    assert "super-secret-internal-detail" not in (bootstrapper.session.login_error or "")
    assert "HTTP 500" in (bootstrapper.session.login_error or "")


@pytest.mark.anyio
@respx.mock
async def test_login_missing_token_key_reports_keys_not_values() -> None:
    # Login succeeds (2xx, valid JSON) but the configured token_response_key
    # isn't present. The response may contain the token under another key, or
    # other sensitive fields — login_error must list keys only, never values.
    respx.post(f"{TARGET}/login").mock(
        return_value=httpx.Response(
            200, json={"access_token": "shhh-this-is-secret", "user": "alice"}
        )
    )
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    bootstrapper = _bootstrapper(auth=_login_flow_auth())
    await bootstrapper.run()
    error = bootstrapper.session.login_error or ""
    assert "shhh-this-is-secret" not in error
    assert "access_token" in error
    assert "user" in error


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


# ── cold-start retry tests ───────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_cold_start_retry_succeeds_on_second_attempt() -> None:
    """Bootstrap retries a 502 and succeeds when the target recovers."""
    respx.post(FULL_URL).mock(
        side_effect=[
            httpx.Response(502),         # cold-start probe 1
            httpx.Response(200),         # recovered
        ]
    )
    with patch("nuguard.common.bootstrap.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        report = await _bootstrapper(startup_retries=BOOTSTRAP_STARTUP_RETRIES).run()

    assert report.all_ok is True
    assert report.checks[0].status == "ok"
    mock_sleep.assert_called_once()   # exactly one backoff sleep before success


@pytest.mark.anyio
@respx.mock
async def test_cold_start_retry_exhausted_raises() -> None:
    """Bootstrap raises TargetUnavailableError when all retries are exhausted."""
    respx.post(FULL_URL).mock(return_value=httpx.Response(503))
    with patch("nuguard.common.bootstrap.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(TargetUnavailableError):
            await _bootstrapper(startup_retries=BOOTSTRAP_STARTUP_RETRIES).run()

    # sleep called once per retry (BOOTSTRAP_STARTUP_RETRIES times total)
    assert mock_sleep.call_count == BOOTSTRAP_STARTUP_RETRIES


@pytest.mark.anyio
@respx.mock
async def test_cold_start_retry_connection_error_then_ok() -> None:
    """Connection refused on first attempt (container not yet up) → retry → ok."""
    respx.post(FULL_URL).mock(
        side_effect=[
            httpx.ConnectError("connection refused"),
            httpx.Response(200),
        ]
    )
    with patch("nuguard.common.bootstrap.asyncio.sleep", new_callable=AsyncMock):
        report = await _bootstrapper(startup_retries=BOOTSTRAP_STARTUP_RETRIES).run()

    assert report.all_ok is True


@pytest.mark.anyio
@respx.mock
async def test_cold_start_retry_not_triggered_for_auth_failure() -> None:
    """Auth failures (401/403) are never retried — they are not cold-start transients."""
    respx.post(FULL_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with patch("nuguard.common.bootstrap.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        report = await _bootstrapper(startup_retries=BOOTSTRAP_STARTUP_RETRIES).run()

    assert report.checks[0].status == "auth_failed"
    mock_sleep.assert_not_called()   # no sleep — auth failures skip retries
