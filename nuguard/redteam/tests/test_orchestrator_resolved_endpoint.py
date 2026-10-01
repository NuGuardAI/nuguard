"""RedteamOrchestrator must keep an endpoint _maybe_probe_endpoints() live-confirmed
when it builds the shared target session, instead of re-resolving it."""
from __future__ import annotations

from typing import Any

import pytest

from nuguard.common.errors import AuthError
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.tests.test_orchestrator_bootstrap_abort import (
    _fake_session_cfg,
    _tiny_orchestrator,
)


async def _run_capturing_resolution(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> tuple[RedteamOrchestrator, dict[str, Any]]:
    captured: dict[str, Any] = {}

    async def _fake_probe(self: RedteamOrchestrator) -> None:
        self._chat_path = "/extract"
        self._chat_payload_key = "text"
        self._chat_path_source = source

    async def _fake_resolve_target_session(**kwargs: Any):
        captured.update(kwargs)
        report = TargetHealthReport(
            target_url="http://target.test",
            endpoint="/extract",
            run_id="r",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://target.test/extract",
                    status="auth_failed",
                    http_status_code=401,
                    error_detail="stop after resolution",
                )
            ],
        )
        return _fake_session_cfg(), report

    monkeypatch.setattr(RedteamOrchestrator, "_maybe_probe_endpoints", _fake_probe)
    monkeypatch.setattr(
        "nuguard.common.session_resolver.resolve_target_session",
        _fake_resolve_target_session,
    )
    orchestrator = _tiny_orchestrator()
    with pytest.raises(AuthError):
        await orchestrator.run()
    return orchestrator, captured


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["probe", "enriched_sbom_cache"])
async def test_live_confirmed_endpoint_is_kept(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    _, captured = await _run_capturing_resolution(monkeypatch, source)

    assert captured["chat_path"] == "/extract"
    assert captured["endpoint_explicit"] is True
    assert captured["payload_key_explicit"] is True
    assert captured["endpoint_source_hint"] == source


@pytest.mark.asyncio
async def test_sbom_guess_is_still_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    _, captured = await _run_capturing_resolution(monkeypatch, "sbom")

    assert captured["endpoint_explicit"] is False
    assert captured["endpoint_source_hint"] is None
