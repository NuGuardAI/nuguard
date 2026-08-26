from __future__ import annotations

from dataclasses import dataclass

import pytest

from nuguard.common.discovery import DiscoveredProfile, DiscoveryOutcome
from nuguard.common.target_verify_public_api import (
    TargetSessionResolveRequest,
    TargetVerifyRequest,
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


@pytest.mark.asyncio
async def test_verify_target_maps_statuses_and_omits_plaintext_credentials(monkeypatch):
    async def _fake_bootstrap_auth_runtime(**kwargs):
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
        return _FakeBootstrapper(session=_FakeAuthSession()), report

    monkeypatch.setattr("nuguard.common.target_verify_public_api.bootstrap_auth_runtime", _fake_bootstrap_auth_runtime)

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
async def test_verify_target_runs_optional_discovery_when_checks_ok(monkeypatch):
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    async def _fake_bootstrap_auth_runtime(**kwargs):
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
        return _FakeBootstrapper(session=_FakeAuthSession()), report

    async def _fake_run_discovery(client, session, request):
        _ = (client, session, request)
        return DiscoveryOutcome(
            profile=DiscoveredProfile(customer_name="Alice", ids=["A-1"], source="live"),
            notes=["discovery ok"],
        )

    monkeypatch.setattr("nuguard.common.target_verify_public_api.bootstrap_auth_runtime", _fake_bootstrap_auth_runtime)
    monkeypatch.setattr("nuguard.common.target_verify_public_api.build_target_app_client", lambda *args, **kwargs: _FakeClient())
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)

    result = await verify_target(TargetVerifyRequest(target_url="http://target"))

    assert result.all_ok is True
    assert result.discovered_profile is not None
    assert result.discovered_profile.customer_name == "Alice"
    assert result.discovery_notes == ["discovery ok"]


@pytest.mark.asyncio
async def test_resolve_target_session_public_uses_probe_endpoint_source(monkeypatch):
    from nuguard.common.session_resolver import TargetSessionConfig

    async def _fake_probe_chat_endpoints(**kwargs):
        _ = kwargs
        return ("/live", "message", False)

    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target",
            endpoint="/live",
            run_id="r2",
            checks=[],
        )
        return (
            TargetSessionConfig(
                base_url="http://target",
                chat_path="/live",
                chat_payload_key="message",
                chat_payload_list=False,
                chat_payload_extras={},
                chat_response_key=None,
                auth_session=_FakeAuthSession(),
                resolution_notes=["used live probe"],
            ),
            report,
        )

    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.discover_chat_config_from_sbom",
        lambda *args, **kwargs: ("", "message", False, None),
    )
    monkeypatch.setattr("nuguard.common.target_verify_public_api.probe_chat_endpoints", _fake_probe_chat_endpoints)
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

    monkeypatch.setattr("nuguard.common.target_verify_public_api.bootstrap_auth_runtime", _fake_bootstrap_auth_runtime)
    monkeypatch.setattr("nuguard.common.target_verify_public_api.build_target_app_client", lambda *args, **kwargs: _FakeClient())
    monkeypatch.setattr("nuguard.common.target_verify_public_api.run_discovery", _fake_run_discovery)
    monkeypatch.setattr("nuguard.common.target_verify_public_api.resolve_target_session", _fake_resolve_target_session)
    monkeypatch.setattr(
        "nuguard.common.target_verify_public_api.discover_chat_config_from_sbom",
        lambda *args, **kwargs: ("/chat", "message", False, None),
    )

    verify_result = await verify_target(TargetVerifyRequest(target_url="http://target"))
    resolve_result = await resolve_target_session_public(
        TargetSessionResolveRequest(target_url="http://target"),
        sbom=object(),
    )

    assert verify_result.all_ok is True
    assert verify_result.endpoint_source in {"config", "default"}
    assert resolve_result.endpoint_source == "sbom"
    assert resolve_result.effective_endpoint == "/chat"
