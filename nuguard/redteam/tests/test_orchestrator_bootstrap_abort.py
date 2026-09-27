"""Regression tests for issue #532: RedteamOrchestrator must abort before any
scenario runs when the default credential's bootstrap health check reports
`auth_failed` or `endpoint_not_found` — instead of proceeding to run scenarios
against a target it already knows can't authenticate or doesn't have the
configured route.
"""
from __future__ import annotations

import pytest

from nuguard.common.errors import AuthError, TargetEndpointNotFoundError
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.sbom.models import AiSbomDocument


def _tiny_orchestrator() -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://target.test",
        concurrency=1,
    )


def _fake_session_cfg():
    from nuguard.common.session_resolver import TargetSessionConfig

    class _FakeAuthSession:
        def headers(self) -> dict[str, str]:
            return {}

    return TargetSessionConfig(
        base_url="http://target.test",
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
        auth_session=_FakeAuthSession(),
        resolution_notes=[],
    )


@pytest.mark.asyncio
async def test_orchestrator_aborts_on_auth_failed_before_any_scenario(monkeypatch) -> None:
    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target.test",
            endpoint="/chat",
            run_id="r1",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target.test/chat",
                    status="auth_failed",
                    http_status_code=401,
                    error_detail="unauthorized",
                )
            ],
        )
        return _fake_session_cfg(), report

    monkeypatch.setattr(
        "nuguard.common.session_resolver.resolve_target_session",
        _fake_resolve_target_session,
    )

    orchestrator = _tiny_orchestrator()
    with pytest.raises(AuthError):
        await orchestrator.run()


@pytest.mark.asyncio
async def test_orchestrator_aborts_on_endpoint_not_found_before_any_scenario(monkeypatch) -> None:
    async def _fake_resolve_target_session(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://target.test",
            endpoint="/chat",
            run_id="r2",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target.test/chat",
                    status="endpoint_not_found",
                    http_status_code=404,
                    error_detail="HTTP 404 — endpoint does not exist at this path",
                )
            ],
        )
        return _fake_session_cfg(), report

    monkeypatch.setattr(
        "nuguard.common.session_resolver.resolve_target_session",
        _fake_resolve_target_session,
    )

    orchestrator = _tiny_orchestrator()
    with pytest.raises(TargetEndpointNotFoundError) as excinfo:
        await orchestrator.run()
    assert excinfo.value.http_status_code == 404
    assert "does not exist" in excinfo.value.detail
