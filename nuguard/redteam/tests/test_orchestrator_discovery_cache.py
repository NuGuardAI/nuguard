"""Tests for RedteamOrchestrator's discovered_profile cache (issue #611 Phase 2).

Drives the real pre-scan discovery block inside RedteamOrchestrator._run_impl()
end to end — bootstrap and discovery HTTP are mocked, scenario generation is
left real against an empty SBOM (nodes=[], edges=[]) so the run completes with
zero scenarios dispatched — to prove:
  - a cache hit (matching discovered_profile_fingerprint) skips run_discovery
  - a cache miss (mismatched or absent fingerprint) runs discovery live
  - a fresh non-empty profile is written onto the SBOM in-memory even with no
    sbom_path, and persisted to disk only when a sbom_path is configured
  - an empty discovery result is never persisted (in-memory or disk)

Also proves the actual cross-tool hand-off this feature exists for: a profile
written by BehaviorRunner is reused by RedteamOrchestrator, and vice versa,
against the same SBOM/target_url/auth_config — see
test_behavior_written_profile_is_reused_by_orchestrator and
test_orchestrator_written_profile_is_reused_by_behavior below.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nuguard.common.auth import AuthConfig, AuthSession
from nuguard.common.discovery import (
    DiscoveredProfile,
    DiscoveryOutcome,
    profile_cache_fingerprint,
)
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.sbom.models import AiSbomDocument

_TARGET_URL = "http://target.test"


class _FakeAuthSession:
    def headers(self) -> dict[str, str]:
        return {}


def _fake_session_cfg():
    from nuguard.common.session_resolver import TargetSessionConfig

    return TargetSessionConfig(
        base_url=_TARGET_URL,
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
        auth_session=_FakeAuthSession(),
        resolution_notes=[],
    )


def _mock_resolve_target_session_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url=_TARGET_URL,
            endpoint="/chat",
            run_id="r-disc-test",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint=f"{_TARGET_URL}/chat",
                    status="ok",
                    http_status_code=200,
                )
            ],
        )
        return _fake_session_cfg(), report

    monkeypatch.setattr("nuguard.common.session_resolver.resolve_target_session", _fake)


class _FakeDiscClient:
    """Only needs to support `async with` — preflight is mocked on the
    instance, and run_discovery/capability-discovery are either mocked or
    disabled, so nothing actually calls into this client's HTTP methods."""

    async def __aenter__(self) -> "_FakeDiscClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _empty_sbom(**kwargs) -> AiSbomDocument:
    return AiSbomDocument(target="unit-test", nodes=[], edges=[], **kwargs)


def _make_orchestrator(
    sbom: AiSbomDocument,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_config: "AuthConfig | None" = None,
    sbom_path=None,
) -> RedteamOrchestrator:
    _mock_resolve_target_session_ok(monkeypatch)
    monkeypatch.setattr(
        "nuguard.common.target_client_builder.build_target_app_client_from_session",
        lambda *a, **kw: _FakeDiscClient(),
    )
    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url=_TARGET_URL,
        sbom_path=sbom_path,
        concurrency=1,
        auth_config=auth_config,
        require_engagement=False,
        capability_discovery=False,
    )
    monkeypatch.setattr(
        orchestrator, "_ensure_endpoint_preflight", AsyncMock(return_value=(True, []))
    )
    return orchestrator


def _make_behavior_runner(sbom: AiSbomDocument, auth_config: "AuthConfig | None"):
    from nuguard.behavior.runner import BehaviorRunner

    cfg = MagicMock()
    cfg.target = _TARGET_URL
    runner = BehaviorRunner(config=cfg, sbom=sbom, policy=None, intent=None, llm_client=None)
    runner._auth_session = AuthSession(auth_config or AuthConfig(type="none"), base_url=_TARGET_URL)
    return runner


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_reuses_cached_profile_and_skips_run_discovery(monkeypatch):
    cached = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom = _empty_sbom(
        discovered_profile=cached.model_dump(mode="json"),
        discovered_profile_fingerprint=profile_cache_fingerprint(_TARGET_URL, None),
    )
    orchestrator = _make_orchestrator(sbom, monkeypatch)
    mock_run_discovery = AsyncMock()
    monkeypatch.setattr("nuguard.common.discovery.run_discovery", mock_run_discovery)

    await orchestrator.run()

    mock_run_discovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_runs_discovery_when_fingerprint_mismatched(monkeypatch):
    stale = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom = _empty_sbom(
        discovered_profile=stale.model_dump(mode="json"),
        discovered_profile_fingerprint=profile_cache_fingerprint("http://staging.test", None),
    )
    orchestrator = _make_orchestrator(sbom, monkeypatch)
    mock_run_discovery = AsyncMock(
        return_value=DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])
    )
    monkeypatch.setattr("nuguard.common.discovery.run_discovery", mock_run_discovery)

    await orchestrator.run()

    mock_run_discovery.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_runs_discovery_when_no_fingerprint_present(monkeypatch):
    """A profile persisted before this field existed (no fingerprint at all)
    is always a cache miss — there is nothing safe to compare it against."""
    stale = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom = _empty_sbom(discovered_profile=stale.model_dump(mode="json"))
    orchestrator = _make_orchestrator(sbom, monkeypatch)
    mock_run_discovery = AsyncMock(
        return_value=DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])
    )
    monkeypatch.setattr("nuguard.common.discovery.run_discovery", mock_run_discovery)

    await orchestrator.run()

    mock_run_discovery.assert_awaited_once()


# ---------------------------------------------------------------------------
# Persistence — in-memory always, disk only with a sbom_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_writes_fresh_profile_in_memory_without_sbom_path(monkeypatch):
    sbom = _empty_sbom()
    orchestrator = _make_orchestrator(sbom, monkeypatch)
    fresh = DiscoveredProfile(customer_name="Bo Chen", ids=["ACCT-9"], source="live")
    monkeypatch.setattr(
        "nuguard.common.discovery.run_discovery",
        AsyncMock(return_value=DiscoveryOutcome(profile=fresh, notes=[])),
    )

    await orchestrator.run()

    assert sbom.discovered_profile is not None
    assert sbom.discovered_profile["customer_name"] == "Bo Chen"
    assert sbom.discovered_profile_fingerprint == profile_cache_fingerprint(_TARGET_URL, None)


@pytest.mark.asyncio
async def test_orchestrator_persists_fresh_profile_to_disk_when_sbom_path_set(monkeypatch, tmp_path):
    sbom = _empty_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    orchestrator = _make_orchestrator(sbom, monkeypatch, sbom_path=sbom_path)
    fresh = DiscoveredProfile(customer_name="Bo Chen", ids=["ACCT-9"], source="live")
    monkeypatch.setattr(
        "nuguard.common.discovery.run_discovery",
        AsyncMock(return_value=DiscoveryOutcome(profile=fresh, notes=[])),
    )
    mock_persist = MagicMock(return_value=sbom_path.with_name("app.sbom.enriched.json"))
    monkeypatch.setattr(
        "nuguard.common.auto_sbom_enricher.persist_discovery_profile_sbom", mock_persist
    )

    await orchestrator.run()

    mock_persist.assert_called_once_with(sbom, sbom_path)


@pytest.mark.asyncio
async def test_orchestrator_does_not_persist_empty_discovery_result(monkeypatch, tmp_path):
    sbom = _empty_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    orchestrator = _make_orchestrator(sbom, monkeypatch, sbom_path=sbom_path)
    monkeypatch.setattr(
        "nuguard.common.discovery.run_discovery",
        AsyncMock(return_value=DiscoveryOutcome(profile=DiscoveredProfile(), notes=[])),
    )
    mock_persist = MagicMock()
    monkeypatch.setattr(
        "nuguard.common.auto_sbom_enricher.persist_discovery_profile_sbom", mock_persist
    )

    await orchestrator.run()

    assert sbom.discovered_profile is None
    assert sbom.discovered_profile_fingerprint is None
    mock_persist.assert_not_called()


# ---------------------------------------------------------------------------
# Cross-tool hand-off (issue #611 Phase 2's actual goal: whoever discovers
# first, the other tool reuses it — run order must not matter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_behavior_written_profile_is_reused_by_orchestrator(monkeypatch):
    """Behavior discovers first; a later redteam run against the same SBOM,
    target_url and auth_config must treat it as a cache hit, not re-run
    discovery."""
    sbom = _empty_sbom()
    auth_config = AuthConfig(type="bearer", header="Authorization: Bearer tok-a")
    runner = _make_behavior_runner(sbom, auth_config)
    profile = DiscoveredProfile(customer_name="Dana Lee", ids=["PT-1"], source="live")
    runner._persist_discovery_profile_sbom(profile, _TARGET_URL)
    assert sbom.discovered_profile is not None  # sanity: the write happened

    orchestrator = _make_orchestrator(sbom, monkeypatch, auth_config=auth_config)
    mock_run_discovery = AsyncMock()
    monkeypatch.setattr("nuguard.common.discovery.run_discovery", mock_run_discovery)

    await orchestrator.run()

    mock_run_discovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_written_profile_is_reused_by_behavior(monkeypatch):
    """Redteam discovers first; a later behavior run against the same SBOM,
    target_url and auth_config must treat it as a cache hit."""
    sbom = _empty_sbom()
    auth_config = AuthConfig(type="bearer", header="Authorization: Bearer tok-a")
    orchestrator = _make_orchestrator(sbom, monkeypatch, auth_config=auth_config)
    fresh = DiscoveredProfile(customer_name="Dana Lee", ids=["PT-1"], source="live")
    monkeypatch.setattr(
        "nuguard.common.discovery.run_discovery",
        AsyncMock(return_value=DiscoveryOutcome(profile=fresh, notes=[])),
    )

    await orchestrator.run()
    assert sbom.discovered_profile is not None  # sanity: the write happened

    runner = _make_behavior_runner(sbom, auth_config)
    cached = runner._cached_discovery_profile(_TARGET_URL)

    assert cached is not None
    assert cached.customer_name == "Dana Lee"
