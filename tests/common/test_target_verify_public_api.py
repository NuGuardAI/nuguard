from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.discovery import DiscoveredProfile, DiscoveryOutcome
from nuguard.common.errors import TargetEndpointNotFoundError
from nuguard.common.target_verify_public_api import (
    TargetSessionResolveRequest,
    TargetVerifyRequest,
    _build_auth_config,
    resolve_target_session_public,
    verify_target,
)
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport


@dataclass
class _FakeAuthSession:
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer token"}

    def login_response_extras(self) -> dict[str, str]:
        return {}


@dataclass
class _FakeBootstrapper:
    session: _FakeAuthSession


def _session_config(*, endpoint_source: str = "default"):
    from nuguard.common.session_resolver import TargetSessionConfig

    return TargetSessionConfig(
        base_url="http://target",
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
        auth_session=_FakeAuthSession(),
        effective_headers={"Authorization": "Bearer token"},
        endpoint_source=endpoint_source,
        resolution_notes=[],
    )


@pytest.mark.asyncio
async def test_verify_target_reports_endpoint_discovery_failure() -> None:
    request = TargetVerifyRequest(target_url="http://target")
    with patch(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        new=AsyncMock(side_effect=TargetEndpointNotFoundError("Could not discover a chat endpoint")),
    ):
        result = await verify_target(request)

    assert result.all_ok is False
    assert result.endpoint_source == "default"
    assert result.checks[0].status == "endpoint_not_found"
    assert "Could not discover a chat endpoint" in (result.checks[0].error_detail or "")


@pytest.mark.parametrize(
    "request_type",
    [TargetVerifyRequest, TargetSessionResolveRequest],
)
def test_public_target_requests_build_login_flow_auth_config(request_type) -> None:
    login_flow = LoginFlowConfig(
        endpoint="/api/auth/login",
        payload={"email": "alice@example.com", "password": "super-secret"},
        token_response_key="data.access_token",
        token_header="X-Session-Token",
        refresh_on_401=False,
    )

    auth_config = _build_auth_config(
        request_type(
            target_url="http://target",
            auth_type="login_flow",
            login_flow=login_flow,
        )
    )

    assert auth_config.type == "login_flow"
    assert auth_config.login_flow == login_flow


@pytest.mark.parametrize(
    "request_type",
    [TargetVerifyRequest, TargetSessionResolveRequest],
)
def test_public_target_requests_require_login_flow_config(request_type) -> None:
    with pytest.raises(ValidationError, match="requires login_flow configuration"):
        request_type(target_url="http://target", auth_type="login_flow")


@pytest.mark.parametrize(
    "request_type",
    [TargetVerifyRequest, TargetSessionResolveRequest],
)
def test_public_target_requests_build_cookie_file_auth_config(request_type, tmp_path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "example.com\tFALSE\t/\tFALSE\t0\tmosaic_session\tabc.def.ghi\n",
        encoding="utf-8",
    )

    auth_config = _build_auth_config(
        request_type(
            target_url="http://target",
            auth_type="cookie_file",
            auth_value=str(cookie_file),
        )
    )

    assert auth_config.type == "cookie_file"
    assert auth_config.cookie_file == str(cookie_file)
    assert auth_config.to_headers() == {"Cookie": "mosaic_session=abc.def.ghi"}


@pytest.mark.parametrize(
    "request_type",
    [TargetVerifyRequest, TargetSessionResolveRequest],
)
def test_public_target_requests_require_cookie_file_path(request_type) -> None:
    with pytest.raises(ValidationError, match="requires auth_value"):
        request_type(target_url="http://target", auth_type="cookie_file")


@pytest.mark.asyncio
async def test_resolve_target_session_public_threads_custom_headers_to_sbom_session(monkeypatch):
    captured_extra_headers = []

    async def _fake_resolve_target_session(**kwargs):
        captured_extra_headers.append(kwargs["extra_headers"])
        report = TargetHealthReport(target_url="http://target", endpoint="/chat", run_id="r-hdr", checks=[])
        return _session_config(), report

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)

    await resolve_target_session_public(
        TargetSessionResolveRequest(
            target_url="http://target",
            headers={"X-Tenant-Id": "acme"},
        ),
        sbom=object(),
    )

    assert captured_extra_headers == [{"X-Tenant-Id": "acme"}]


@pytest.mark.asyncio
async def test_verify_target_passes_login_flow_to_shared_resolver(monkeypatch) -> None:
    login_flow = LoginFlowConfig(
        endpoint="/api/auth/login",
        payload={"email": "alice@example.com", "password": "super-secret"},
    )
    captured_auth_configs = []

    async def _fake_resolve_target_session(**kwargs):
        captured_auth_configs.append(kwargs["auth_config"])
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r-login-flow",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="login_flow",
                    endpoint="http://target/chat",
                    status="auth_failed",
                    http_status_code=401,
                )
            ],
        )
        return _session_config(), report

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)

    await verify_target(
        TargetVerifyRequest(
            target_url="http://target",
            auth_type="login_flow",
            login_flow=login_flow,
        )
    )

    assert captured_auth_configs == [AuthConfig(type="login_flow", login_flow=login_flow)]


@pytest.mark.asyncio
async def test_resolve_target_session_passes_login_flow_to_shared_resolver(monkeypatch) -> None:
    login_flow = LoginFlowConfig(endpoint="/api/auth/login", payload={"api_key": "super-secret"})
    captured_auth_configs = []

    async def _fake_resolve_target_session(**kwargs):
        captured_auth_configs.append(kwargs["auth_config"])
        return _session_config(), TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r-login-flow",
            checks=[],
        )

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)

    await resolve_target_session_public(
        TargetSessionResolveRequest(
            target_url="http://target",
            auth_type="login_flow",
            login_flow=login_flow,
        )
    )

    assert captured_auth_configs == [AuthConfig(type="login_flow", login_flow=login_flow)]


@pytest.mark.asyncio
async def test_verify_target_maps_statuses_and_omits_plaintext_credentials(monkeypatch):
    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r1",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="basic",
                    endpoint="http://target/chat",
                    status="auth_failed",
                    http_status_code=401,
                    error_detail="unauthorized",
                )
            ],
        )
        return _session_config(), report

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)

    result = await verify_target(
        TargetVerifyRequest(
            target_url="http://target",
            auth_type="basic",
            auth_username="alice",
            auth_password="super-secret",
        )
    )

    assert result.all_ok is False
    assert result.checks[0].status == "auth_failed"
    dumped = result.model_dump_json()
    assert "super-secret" not in dumped
    assert "alice" not in dumped


@pytest.mark.asyncio
async def test_verify_target_maps_endpoint_not_found_and_payload_hint(monkeypatch):
    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r-enf",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target/chat",
                    status="endpoint_not_found",
                    http_status_code=404,
                    error_detail="HTTP 404 — endpoint does not exist at this path",
                ),
            ],
        )
        return _session_config(), report

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"))

    assert result.all_ok is False
    assert result.checks[0].status == "endpoint_not_found"
    assert result.checks[0].http_status_code == 404
    # Discovery must not run against an endpoint that's confirmed not to exist.
    assert result.discovered_profile is None


@pytest.mark.asyncio
async def test_verify_target_passes_through_payload_hint(monkeypatch):
    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r-hint",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target/chat",
                    status="ok",
                    http_status_code=400,
                    payload_hint="HTTP 400 — endpoint exists but rejected the probe payload",
                ),
            ],
        )
        return _session_config(), report

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *args, **kwargs: _FakeClient(),
    )
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"))

    assert result.all_ok is True
    assert result.checks[0].payload_hint == "HTTP 400 — endpoint exists but rejected the probe payload"


@pytest.mark.asyncio
async def test_verify_target_runs_optional_discovery_when_checks_ok(monkeypatch):
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r1",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target/chat",
                    status="ok",
                    http_status_code=200,
                )
            ],
        )
        return _session_config(), report

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(
            profile=DiscoveredProfile(customer_name="Alice", ids=["A-1"], source="live"),
            notes=["discovery ok"],
        )

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)
    monkeypatch.setattr("nuguard.common.target_verify_public_api.build_target_app_client_from_session", lambda *args, **kwargs: _FakeClient())
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"))

    assert result.all_ok is True
    assert result.discovered_profile is not None
    assert result.discovered_profile.customer_name == "Alice"
    assert result.discovery_notes == ["discovery ok"]


@pytest.mark.asyncio
async def test_resolve_target_session_public_uses_probe_endpoint_source(monkeypatch):
    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/live",
            run_id="r2",
            checks=[],
        )
        session = _session_config(endpoint_source="probe")
        session.chat_path = "/live"
        session.resolution_notes = ["used live probe"]
        return session, report

    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)

    result = await resolve_target_session_public(
        TargetSessionResolveRequest(
            target_url="http://target",
            auth_type="bearer",
            auth_value="very-secret-token",
        ),
        sbom=object(),
    )

    assert result.endpoint_source == "probe"
    assert result.effective_endpoint == "/live"
    dumped = result.model_dump_json()
    assert "very-secret-token" not in dumped


@pytest.mark.asyncio
async def test_resolve_target_session_public_resolves_sbom_host_before_planning(monkeypatch):
    resolved_session_target_urls = []

    async def _fake_resolve_target_session(**kwargs):
        resolved_session_target_urls.append(kwargs["target_url"])
        session = _session_config()
        session.base_url = kwargs["target_url"]
        return session, TargetHealthReport(
            target_url=kwargs["target_url"],
            endpoint="/chat",
            run_id="r-resolved-url",
            checks=[],
        )

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session,
    )

    result = await resolve_target_session_public(
        TargetSessionResolveRequest(target_url="https://static.example.test"),
        sbom=object(),
    )

    assert resolved_session_target_urls == ["https://static.example.test"]
    assert result.effective_target_url == "https://static.example.test"


@pytest.mark.asyncio
async def test_parity_tv_001(monkeypatch):
    from nuguard.common.session_resolver import TargetSessionConfig

    async def _fake_bootstrap_auth_runtime(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r-tv",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target/chat",
                    status="ok",
                    http_status_code=200,
                )
            ],
        )
        return _FakeBootstrapper(session=_FakeAuthSession()), report

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(profile=DiscoveredProfile(customer_name="A", ids=["ID-1"], source="live"), notes=[])

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(target_url="http://target", endpoint="/chat", run_id="r-tv2", checks=[])
        return (
            TargetSessionConfig(
                base_url="http://target",
                chat_path="/chat",
                chat_payload_key="message",
                chat_payload_list=False,
                chat_payload_extras={},
                chat_response_key=None,
                auth_session=_FakeAuthSession(),
                resolution_notes=["resolved"],
            ),
            report,
        )

    monkeypatch.setattr("nuguard.common.target_verify_public_api.build_target_app_client_from_session", lambda *args, **kwargs: _FakeClient())
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)
    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)
    # resolve_chat_endpoint is no longer called by verify_target/resolve_target_session_public
    # (both route through resolve_target_session directly, mocked above) — the endpoint/payload
    # resolution import (EndpointSource, PayloadShape, ResolvedEndpoint) at the top of this test
    # is kept only because other tests in this file still use it.

    verify_result = await verify_target(TargetVerifyRequest(target_url="http://target"))
    resolve_result = await resolve_target_session_public(
        TargetSessionResolveRequest(target_url="http://target"),
        sbom=object(),
    )

    assert verify_result.all_ok is True
    assert verify_result.endpoint_source in {"config", "default"}
    assert resolve_result.endpoint_source == verify_result.endpoint_source
    assert resolve_result.effective_endpoint == "/chat"


# ---------------------------------------------------------------------------
# verify_target() runs endpoint/path-param preflight before discovery
# (issue #611) — unit tests mock validate_and_rotate_chat_endpoint directly,
# the same way the rest of this file mocks resolve_target_session/run_discovery
# at the module-import boundary; validate_and_rotate_chat_endpoint's own
# internal correctness (candidate rotation, bootstrap) is covered by
# nuguard/common/tests/test_endpoint_preflight.py.
# ---------------------------------------------------------------------------

class _PreflightFakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


def _fake_resolve_target_session_factory(session_cfg):
    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/chat",
            run_id="r-pf",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target/chat",
                    status="ok",
                    http_status_code=200,
                )
            ],
        )
        return session_cfg, report

    return _fake_resolve_target_session


@pytest.mark.asyncio
async def test_verify_target_calls_preflight_before_discovery(monkeypatch):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    calls: list[dict] = []

    async def _fake_preflight(client, sbom, **kwargs):
        _ = (client, sbom)
        calls.append(kwargs)
        return PreflightOutcome(ok=True)

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *args, **kwargs: _PreflightFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _fake_preflight,
    )
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    await verify_target(TargetVerifyRequest(target_url="http://target"))

    assert len(calls) == 1
    assert calls[0]["target_url"] == "http://target"
    assert calls[0]["max_candidates"] == 3  # TargetVerifyRequest default


@pytest.mark.asyncio
async def test_verify_target_passes_configured_preflight_candidates(monkeypatch):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    calls: list[dict] = []

    async def _fake_preflight(client, sbom, **kwargs):
        _ = (client, sbom)
        calls.append(kwargs)
        return PreflightOutcome(ok=True)

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *args, **kwargs: _PreflightFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _fake_preflight,
    )
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    await verify_target(
        TargetVerifyRequest(target_url="http://target", preflight_candidates=7)
    )

    assert calls[0]["max_candidates"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_kwargs", "expected_explicit"),
    [
        ({"target_url": "http://target"}, False),
        ({"target_url": "http://target", "chat_path": "/custom"}, True),
    ],
)
async def test_verify_target_threads_has_explicit_endpoint(
    monkeypatch, request_kwargs, expected_explicit
):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    calls: list[dict] = []

    async def _fake_preflight(client, sbom, **kwargs):
        _ = (client, sbom)
        calls.append(kwargs)
        return PreflightOutcome(ok=True)

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *args, **kwargs: _PreflightFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _fake_preflight,
    )
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    await verify_target(TargetVerifyRequest(**request_kwargs))

    assert calls[0]["has_explicit_endpoint"] is expected_explicit


@pytest.mark.asyncio
async def test_verify_target_updates_endpoint_on_rotation(monkeypatch):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    async def _fake_preflight(client, sbom, **kwargs):
        _ = (client, sbom, kwargs)
        return PreflightOutcome(
            ok=True,
            rotated_endpoint=("/rotated/chat", "message", False, None),
            endpoint_source="sbom",
            notes=["rotated to /rotated/chat"],
        )

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config(endpoint_source="probe")),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *args, **kwargs: _PreflightFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _fake_preflight,
    )
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"))

    assert result.endpoint == "/rotated/chat"
    assert result.discovered_endpoint == "/rotated/chat"
    assert result.endpoint_source == "sbom"
    assert "rotated to /rotated/chat" in result.discovery_notes


@pytest.mark.asyncio
async def test_verify_target_skips_discovery_when_preflight_fails(monkeypatch):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    discovery_called = False

    async def _fake_preflight(client, sbom, **kwargs):
        _ = (client, sbom, kwargs)
        return PreflightOutcome(ok=False, notes=["no working chat endpoint found"])

    async def _fake_run_discovery(client, session, request):
        nonlocal discovery_called
        discovery_called = True
        _ = (client, session, request)
        return DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *args, **kwargs: _PreflightFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _fake_preflight,
    )
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"))

    assert discovery_called is False
    assert result.all_ok is False
    assert result.discovered_profile is None
    assert "no working chat endpoint found" in result.discovery_notes
    # Structured signal, not just free text: a caller inspecting `checks`
    # alone must be able to see why all_ok is False.
    endpoint_checks = [c for c in result.checks if c.identity == "endpoint"]
    assert len(endpoint_checks) == 1
    assert endpoint_checks[0].status == "endpoint_not_found"
    assert "no working chat endpoint found" in endpoint_checks[0].error_detail


# ---------------------------------------------------------------------------
# End-to-end regression test for issue #611: a templated, two-step chat
# endpoint (create a conversation, then POST to .../:id/messages) resolves
# and discovers a profile, instead of every discovery turn failing with
# "[CONFIG_ERROR: unresolved path param 'id']". Uses a real TargetAppClient
# (via build_target_app_client_from_session, not mocked) against a respx-
# mocked HTTP transport, so this exercises the actual production code path —
# only resolve_target_session is faked, to avoid re-driving the full auth
# bootstrap flow, which is unrelated to this bug.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_target_resolves_templated_endpoint_and_discovers_profile(monkeypatch):
    import uuid as _uuid

    import httpx
    import respx

    from nuguard.common.session_resolver import TargetSessionConfig
    from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
    from nuguard.sbom.types import ComponentType

    chat_path = "/chat/conversations/:id/messages"
    source_path = "/chat/conversations"
    chat_node = Node(
        id=_uuid.uuid5(_uuid.NAMESPACE_URL, f"API_ENDPOINT/{chat_path}"),
        name=chat_path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.99,
        metadata=NodeMetadata(
            endpoint=chat_path,
            method="POST",
            path_params=["id"],
            path_param_sources={"id": source_path},
        ),
    )
    source_node = Node(
        id=_uuid.uuid5(_uuid.NAMESPACE_URL, f"API_ENDPOINT/{source_path}"),
        name=source_path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.99,
        metadata=NodeMetadata(endpoint=source_path, method="POST"),
    )
    sbom = AiSbomDocument(target="./app", nodes=[chat_node, source_node])

    session_cfg = TargetSessionConfig(
        base_url="http://target-app.test",
        chat_path=chat_path,
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key="reply",
        auth_session=_FakeAuthSession(),
        effective_headers={},
        endpoint_source="sbom",
        resolution_notes=[],
    )

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(session_cfg),
    )

    with respx.mock:
        create_route = respx.post("http://target-app.test/chat/conversations").mock(
            return_value=httpx.Response(201, json={"id": "conv-abc123"})
        )
        message_route = respx.post(
            "http://target-app.test/chat/conversations/conv-abc123/messages"
        ).mock(
            return_value=httpx.Response(
                200, json={"reply": "Name: Alice Johnson. Account ID: ACCT-1001."}
            )
        )

        result = await verify_target(
            TargetVerifyRequest(target_url="http://target-app.test"), sbom=sbom
        )

        assert create_route.called
        assert message_route.called
        assert result.endpoint == chat_path
        assert result.discovered_profile is not None
        assert not any("CONFIG_ERROR" in note for note in result.discovery_notes)


@pytest.mark.asyncio
async def test_verify_target_reflects_real_rotation_to_a_working_candidate(monkeypatch):
    """End-to-end, not mocked: the primary endpoint answers HTTP 400 (a status
    bootstrap's health-check classifies as "ok" with a payload_hint — see
    bootstrap.py — but validate_and_rotate_chat_endpoint's stricter preflight
    check flags as a wrong-route signal), and a second SBOM-declared endpoint
    answers conversationally. Proves verify_target() correctly reflects a
    *real* rotation outcome, not just that it reacts to a mocked
    PreflightOutcome (which every other rotation test in this file uses)."""
    import httpx
    import respx

    from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
    from nuguard.sbom.types import ComponentType

    alt_endpoint = "/api/better-chat"
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.9,
                metadata=NodeMetadata(endpoint="/chat", method="POST"),
            ),
            Node(
                name="better_chat_endpoint",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(endpoint=alt_endpoint, method="POST"),
            ),
        ],
    )

    session_cfg = _session_config(endpoint_source="sbom")
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(session_cfg),
    )

    with respx.mock:
        bad_route = respx.post("http://target/chat").mock(
            return_value=httpx.Response(400, json={"error": "bad request"})
        )
        good_route = respx.post(f"http://target{alt_endpoint}").mock(
            return_value=httpx.Response(
                200, json={"response": "Name: Alice Johnson. Account ID: ACCT-1001."}
            )
        )

        result = await verify_target(
            TargetVerifyRequest(target_url="http://target"), sbom=sbom
        )

        assert bad_route.called
        assert good_route.called
        assert result.endpoint == alt_endpoint
        assert result.discovered_endpoint == alt_endpoint
        assert result.endpoint_source in ("sbom", "probe")
        assert result.discovered_profile is not None
        assert result.discovered_profile.customer_name == "Alice Johnson"


class _TrackingBootstrapClient:
    """Minimal, fully-controlled TargetAppClient stand-in for proving
    validate_and_rotate_chat_endpoint's resource-amplification behavior
    deterministically — a real TargetAppClient against respx can't be used
    here because endpoint_detection's live-probe fallback (a long built-in
    list of generic path guesses, unrelated to anything declared in the
    test's SBOM) outranks/out-competes small, hand-built SBOM candidate
    sets in ways that make which candidates actually get tried
    non-deterministic from a test's point of view. This fake mirrors the
    shape of _BootstrapDummyClient in
    nuguard/common/tests/test_endpoint_preflight.py, except it tracks every
    set_path_param call with which chat_path was active at the time,
    instead of collapsing to a single "bootstrapped: yes/no" flag — needed
    to distinguish "candidate A bootstrapped" from "candidate B bootstrapped"
    rather than just "something got bootstrapped at some point".
    """

    def __init__(self, working_path: str, initial_path: str) -> None:
        self.chat_path = initial_path
        self.working_path = working_path
        self.path_param_values: dict[str, str] = {}
        self.bootstrap_calls: list[tuple[str, str, str]] = []  # (chat_path, param, value)

    async def send(self, message: str, session: object) -> tuple[str, list[dict]]:
        _ = (message, session)
        if self.chat_path == self.working_path and self.path_param_values:
            return "Name: Alice Johnson. Account ID: ACCT-1001.", []
        return "[HTTP 400] bad request", []

    async def invoke_endpoint(
        self, path: str, method: str = "POST", body: dict | None = None, **_kw: object
    ) -> tuple[int, str, dict]:
        _ = (method, body)
        return 201, "", {"id": f"id-for-{path}"}

    def set_chat_endpoint(self, chat_path: str, *_a: object, **_kw: object) -> None:
        self.chat_path = chat_path
        self.path_param_values = {}  # mirrors real TargetAppClient.set_chat_endpoint

    def set_path_param(self, name: str, value: str) -> None:
        self.path_param_values[name] = value
        self.bootstrap_calls.append((self.chat_path, name, value))


@pytest.mark.asyncio
async def test_rotation_bootstraps_path_param_for_every_templated_candidate_tested():
    """When rotation tests multiple ranked candidates and more than one is
    itself templated, the prerequisite resource gets created once per
    templated candidate tried — not just once for whichever one eventually
    wins — and the eventual winner gets bootstrapped a *second* time on
    top of that, since set_chat_endpoint clears bound path params on every
    candidate switch, including the final "re-select the winner" step (see
    endpoint_preflight.py's own comment there). This is pre-existing
    validate_and_rotate_chat_endpoint behavior, newly reachable through
    Target Verify by this fix (issue #611) — pinning it down with an
    assertion rather than leaving it as an unverified review comment."""
    from nuguard.common.endpoint_preflight import validate_and_rotate_chat_endpoint
    from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
    from nuguard.sbom.types import ComponentType

    primary_chat = "/chat/sessions/:id/messages"
    primary_source = "/internal/create-session"
    alt_chat = "/chat/convos/:id/messages"
    alt_source = "/internal/create-convo"

    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            Node(
                name="primary_chat",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.9,
                metadata=NodeMetadata(
                    endpoint=primary_chat, method="POST", chat_payload_key="message",
                    path_params=["id"], path_param_sources={"id": primary_source},
                ),
            ),
            Node(
                name="alt_chat",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(
                    endpoint=alt_chat, method="POST", chat_payload_key="message",
                    path_params=["id"], path_param_sources={"id": alt_source},
                ),
            ),
        ],
    )

    client = _TrackingBootstrapClient(working_path=alt_chat, initial_path=primary_chat)

    outcome = await validate_and_rotate_chat_endpoint(
        client, sbom, has_explicit_endpoint=False, target_url="http://target"
    )

    assert outcome.ok is True
    assert outcome.rotated_endpoint is not None
    assert outcome.rotated_endpoint[0] == alt_chat

    bootstrapped_paths = [call[0] for call in client.bootstrap_calls]
    # The rejected primary candidate was still bootstrapped once to be
    # testable at all...
    assert bootstrapped_paths.count(primary_chat) == 1
    # ...and the winning candidate was bootstrapped twice: once while being
    # scored, once more when re-selected as the final winner.
    assert bootstrapped_paths.count(alt_chat) == 2


# ---------------------------------------------------------------------------
# Test F: preflight_candidates=0 disables SBOM-candidate rotation testing,
# without crashing — for a caller who wants path-param bootstrapping on the
# current endpoint but not candidate-rotation testing.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zero_preflight_candidates_disables_rotation_without_crashing(monkeypatch):
    """max_candidates=0 means the SBOM-candidate loop never runs at all
    (ranked_candidates[:0] == []) — the alternate, working candidate is never
    tried or bootstrapped, even though it exists and would have won with the
    default of 3. The live-probe-fallback stage that follows a failed SBOM
    rotation is unrelated to max_candidates and still runs — mocked here to
    return "nothing found" immediately, since it otherwise makes real,
    sequential network connection attempts against ~20 hardcoded guess paths
    (confirmed empirically: ~15s+ unmocked) that have nothing to do with
    what max_candidates=0 is actually supposed to test."""
    from nuguard.common.endpoint_preflight import validate_and_rotate_chat_endpoint
    from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
    from nuguard.sbom.types import ComponentType

    async def _fake_probe_endpoint(*args, **kwargs):
        _ = (args, kwargs)
        return None

    monkeypatch.setattr(
        "nuguard.common.endpoint_detection.live_probe.probe_endpoint", _fake_probe_endpoint
    )

    primary_chat = "/chat/sessions/:id/messages"
    primary_source = "/internal/create-session"
    alt_chat = "/chat/convos/:id/messages"
    alt_source = "/internal/create-convo"

    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            Node(
                name="primary_chat",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.9,
                metadata=NodeMetadata(
                    endpoint=primary_chat, method="POST", chat_payload_key="message",
                    path_params=["id"], path_param_sources={"id": primary_source},
                ),
            ),
            Node(
                name="alt_chat",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(
                    endpoint=alt_chat, method="POST", chat_payload_key="message",
                    path_params=["id"], path_param_sources={"id": alt_source},
                ),
            ),
        ],
    )

    # alt_chat is the working endpoint — would win rotation at the default
    # max_candidates=3 (proven by the sibling test above), but must never
    # even be tried here.
    client = _TrackingBootstrapClient(working_path=alt_chat, initial_path=primary_chat)

    outcome = await validate_and_rotate_chat_endpoint(
        client, sbom, has_explicit_endpoint=False, target_url="http://target",
        max_candidates=0,
    )

    assert outcome.ok is False  # nothing else was tried, so nothing was found
    bootstrapped_paths = [call[0] for call in client.bootstrap_calls]
    assert bootstrapped_paths == [primary_chat]  # only the original, never alt_chat


# ---------------------------------------------------------------------------
# Test G: resolve_target_session_public() is explicitly out of scope for
# this fix (documented in issue #611 — it never builds a discovery client at
# all, so there's nothing to preflight-validate). Regression guard against
# accidental future scope creep silently wiring it in.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_target_session_public_does_not_run_endpoint_preflight(monkeypatch):
    called = False

    async def _fake_preflight(*args, **kwargs):
        nonlocal called
        called = True
        _ = (args, kwargs)
        from nuguard.common.endpoint_preflight import PreflightOutcome
        return PreflightOutcome(ok=True)

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _fake_preflight,
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )

    await resolve_target_session_public(
        TargetSessionResolveRequest(target_url="http://target")
    )

    assert called is False


# ---------------------------------------------------------------------------
# resolved_chat_endpoint cache (issue #611 Phase 3) — verify_target() must
# check the SBOM for a previously-validated resolution before running live
# preflight, and persist a fresh one after. Mirrors
# nuguard/redteam/tests/test_orchestrator_endpoint_cache.py for this third
# call site of validate_and_rotate_chat_endpoint.
# ---------------------------------------------------------------------------


class _CacheAwareFakeClient:
    def __init__(self, chat_path: str = "/chat") -> None:
        self.chat_path = chat_path
        self.path_param_values: dict[str, str] = {}
        self._chat_payload_key = "message"
        self._chat_payload_list = False
        self._chat_response_key: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False

    def set_chat_endpoint(self, path, payload_key, payload_list, response_key=None) -> None:
        self.chat_path = path
        self._chat_payload_key = payload_key
        self._chat_payload_list = payload_list
        self._chat_response_key = response_key

    def set_path_param(self, name: str, value: str) -> None:
        self.path_param_values[name] = value


def _empty_discovery_sbom(**kwargs):
    from nuguard.sbom.models import AiSbomDocument

    return AiSbomDocument(target="./app", nodes=[], edges=[], **kwargs)


@pytest.mark.asyncio
async def test_verify_target_cache_hit_skips_live_preflight(monkeypatch):
    from nuguard.common.endpoint_preflight import persist_endpoint_resolution

    sbom = _empty_discovery_sbom()
    persist_endpoint_resolution(
        sbom, "http://target", AuthConfig(type="none"),
        chat_path="/cached/path", chat_payload_key="messages", chat_payload_list=True,
        chat_response_key="reply", endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    mock_validate = _make_async_mock_tracker()
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint", mock_validate
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.run_discovery",
        _async_return(DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])),
    )

    result = await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom)

    assert mock_validate.calls == []
    assert result.endpoint == "/cached/path"
    assert result.all_ok is True


@pytest.mark.asyncio
async def test_verify_target_non_explicit_hit_does_not_inherit_config_source_label(monkeypatch):
    """Regression: a cache entry written by an earlier EXPLICIT run must not
    make a later NON-explicit verify_target() call falsely report
    endpoint_source="config" — this call never configured chat_path itself."""
    from nuguard.common.endpoint_preflight import persist_endpoint_resolution

    sbom = _empty_discovery_sbom()
    persist_endpoint_resolution(
        sbom, "http://target", AuthConfig(type="none"),
        chat_path="/chat", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="config", path_param_values={},
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _make_async_mock_tracker(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.run_discovery",
        _async_return(DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])),
    )

    result = await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom)

    assert result.endpoint_source != "config"


@pytest.mark.asyncio
async def test_verify_target_cache_miss_runs_live_preflight_and_persists(monkeypatch):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    sbom = _empty_discovery_sbom()

    async def _fake_preflight(client, sbom_arg, **kwargs):
        _ = (client, sbom_arg, kwargs)
        return PreflightOutcome(
            ok=True, rotated_endpoint=("/new/path", "message", False, None),
            endpoint_source="sbom", notes=[],
        )

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint", _fake_preflight
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.run_discovery",
        _async_return(DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])),
    )

    result = await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom)

    assert result.endpoint == "/new/path"
    assert sbom.resolved_chat_endpoint is not None
    assert sbom.resolved_chat_endpoint["chat_path"] == "/new/path"
    assert sbom.resolved_chat_endpoint_fingerprint is not None


@pytest.mark.asyncio
async def test_verify_target_persists_to_disk_when_sbom_path_set(monkeypatch, tmp_path):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    sbom = _empty_discovery_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())

    async def _fake_preflight(client, sbom_arg, **kwargs):
        _ = (client, sbom_arg, kwargs)
        return PreflightOutcome(ok=True, notes=[])

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint", _fake_preflight
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.run_discovery",
        _async_return(DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])),
    )

    await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom, sbom_path=sbom_path)

    import json

    written = json.loads(sbom_path.with_name("app.sbom.enriched.json").read_text())
    assert written["resolved_chat_endpoint"]["chat_path"] == "/chat"


@pytest.mark.asyncio
async def test_verify_target_explicit_endpoint_ignores_cache_for_different_path(monkeypatch):
    """Config-wins safeguard: a cached resolution for a DIFFERENT path than
    the explicitly configured chat_path must never be used — live preflight
    always runs on the explicit path instead."""
    from nuguard.common.endpoint_preflight import PreflightOutcome, persist_endpoint_resolution

    sbom = _empty_discovery_sbom()
    persist_endpoint_resolution(
        sbom, "http://target", AuthConfig(type="none"),
        chat_path="/some/other/sbom/path", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    mock_validate = _make_async_mock_tracker(
        PreflightOutcome(ok=True, notes=[])
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(chat_path="/explicitly/configured"),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint", mock_validate
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.run_discovery",
        _async_return(DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])),
    )

    await verify_target(
        TargetVerifyRequest(target_url="http://target", chat_path="/explicitly/configured"), sbom=sbom
    )

    assert len(mock_validate.calls) == 1
    assert mock_validate.calls[0]["has_explicit_endpoint"] is True


def _make_async_mock_tracker(return_value=None):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    if return_value is None:
        return_value = PreflightOutcome(ok=True, notes=[])

    async def _tracked(client, sbom_arg, **kwargs):
        _ = (client, sbom_arg)
        _tracked.calls.append(kwargs)
        return return_value

    _tracked.calls = []
    return _tracked


def _async_return(value):
    async def _inner(*args, **kwargs):
        _ = (args, kwargs)
        return value

    return _inner


# ---------------------------------------------------------------------------
# discovered_profile cache (issue #611 Phase 2) — verify_target() must check
# the SBOM for a previously-discovered profile before running the live
# DISCOVER conversation, and persist a fresh one after. This mirrors the
# resolved_chat_endpoint cache tests above but was missing entirely until
# now: Phase 2 wired discovered_profile caching into BehaviorRunner and
# RedteamOrchestrator but never into verify_target(), so every call re-ran
# discovery live regardless of a prior successful discovery against the
# same target/auth.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_target_discovered_profile_cache_hit_skips_run_discovery(monkeypatch):
    from nuguard.common.discovery import profile_cache_fingerprint
    from nuguard.common.endpoint_preflight import PreflightOutcome

    sbom = _empty_discovery_sbom()
    cached = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom.discovered_profile = cached.model_dump(mode="json")
    sbom.discovered_profile_fingerprint = profile_cache_fingerprint("http://target", AuthConfig(type="none"))

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _async_return(PreflightOutcome(ok=True)),
    )
    mock_run_discovery = _make_async_mock_tracker()
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", mock_run_discovery)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom)

    assert mock_run_discovery.calls == []
    assert result.discovered_profile is not None
    assert result.discovered_profile.customer_name == "Asha Patel"
    assert "Pre-scan discovery (from enriched SBOM)" in " ".join(result.discovery_notes)


@pytest.mark.asyncio
async def test_verify_target_discovered_profile_cache_miss_runs_and_persists(monkeypatch):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    sbom = _empty_discovery_sbom()
    fresh = DiscoveredProfile(customer_name="Bo Chen", ids=["ACCT-9"], source="live")

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _async_return(PreflightOutcome(ok=True)),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.run_discovery",
        _async_return(DiscoveryOutcome(profile=fresh, notes=[])),
    )

    result = await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom)

    assert result.discovered_profile is not None
    assert result.discovered_profile.customer_name == "Bo Chen"
    assert sbom.discovered_profile is not None
    assert sbom.discovered_profile["customer_name"] == "Bo Chen"
    assert sbom.discovered_profile_fingerprint is not None


@pytest.mark.asyncio
async def test_verify_target_persists_discovered_profile_to_disk_when_sbom_path_set(monkeypatch, tmp_path):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    sbom = _empty_discovery_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    fresh = DiscoveredProfile(customer_name="Bo Chen", ids=["ACCT-9"], source="live")

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _async_return(PreflightOutcome(ok=True)),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.run_discovery",
        _async_return(DiscoveryOutcome(profile=fresh, notes=[])),
    )

    await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom, sbom_path=sbom_path)

    import json

    written = json.loads(sbom_path.with_name("app.sbom.enriched.json").read_text())
    assert written["discovered_profile"]["customer_name"] == "Bo Chen"
    assert written["discovered_profile_fingerprint"]


@pytest.mark.asyncio
async def test_verify_target_auth_mismatch_causes_independent_rediscovery(monkeypatch):
    from unittest.mock import AsyncMock

    from nuguard.common.discovery import profile_cache_fingerprint
    from nuguard.common.endpoint_preflight import PreflightOutcome

    sbom = _empty_discovery_sbom()
    stale = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom.discovered_profile = stale.model_dump(mode="json")
    # Cached against a DIFFERENT target than this call will resolve to.
    sbom.discovered_profile_fingerprint = profile_cache_fingerprint("http://a-different-target", None)

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.resolve_target_session",
        _fake_resolve_target_session_factory(_session_config()),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
        lambda *a, **kw: _CacheAwareFakeClient(),
    )
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
        _async_return(PreflightOutcome(ok=True)),
    )
    fresh = DiscoveredProfile(customer_name="Carla Diaz", ids=["ACCT-5"], source="live")
    mock_run_discovery = AsyncMock(return_value=DiscoveryOutcome(profile=fresh, notes=[]))
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", mock_run_discovery)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"), sbom=sbom)

    # A genuine re-discovery attempt was made rather than reusing the stale
    # cache from "a-different-target".
    mock_run_discovery.assert_awaited_once()
    assert result.discovered_profile is not None
    assert result.discovered_profile.customer_name == "Carla Diaz"
    assert sbom.discovered_profile["customer_name"] == "Carla Diaz"
