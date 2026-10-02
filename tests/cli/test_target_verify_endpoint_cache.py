"""Tests for _run_pre_scan_discovery's SBOM-backed chat-endpoint resolution
cache (issue #611 Phase 3) — the CLI's own independent implementation of the
same caching logic covered for the other three call sites by:
  - nuguard/common/tests/test_endpoint_preflight_cache.py (unit level)
  - nuguard/redteam/tests/test_orchestrator_endpoint_cache.py
  - nuguard/behavior/tests/test_runner.py
  - tests/common/test_target_verify_public_api.py (verify_target() public API)

Calls _run_pre_scan_discovery directly with a fully-controlled fake client
rather than driving the full CLI command through CliRunner+respx — this
file's sibling test_target_verify_discovery.py already documents that
candidate-ranking/live-probe-fallback internals are too non-deterministic to
drive reliably from CLI-level tests when what's actually being tested is
cache control flow, not HTTP behavior.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nuguard.cli.commands.target import _run_pre_scan_discovery
from nuguard.common.auth import AuthConfig
from nuguard.common.discovery import DiscoveredProfile
from nuguard.common.endpoint_preflight import PreflightOutcome, persist_endpoint_resolution
from nuguard.common.session_resolver import TargetSessionConfig
from nuguard.sbom.models import AiSbomDocument

_TARGET_URL = "http://target.test"


class _FakeAuthSession:
    def __init__(self, auth_config: "AuthConfig | None" = None) -> None:
        self.auth_config = auth_config

    def headers(self) -> dict[str, str]:
        return {}


class _FakeDiscoveryClient:
    def __init__(self, chat_path: str = "/chat") -> None:
        self.chat_path = chat_path
        self.path_param_values: dict[str, str] = {}
        self._chat_payload_key = "message"
        self._chat_payload_list = False
        self._chat_response_key: str | None = None

    async def __aenter__(self) -> "_FakeDiscoveryClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def set_chat_endpoint(self, path, payload_key, payload_list, response_key=None) -> None:
        self.chat_path = path
        self._chat_payload_key = payload_key
        self._chat_payload_list = payload_list
        self._chat_response_key = response_key

    def set_path_param(self, name: str, value: str) -> None:
        self.path_param_values[name] = value


def _session_cfg(*, chat_path: str = "/chat", auth_config: "AuthConfig | None" = None) -> TargetSessionConfig:
    return TargetSessionConfig(
        base_url=_TARGET_URL,
        chat_path=chat_path,
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
        auth_session=_FakeAuthSession(auth_config),
        resolution_notes=[],
    )


def _empty_sbom(**kwargs) -> AiSbomDocument:
    return AiSbomDocument(target="./app", nodes=[], edges=[], **kwargs)


def _mock_discovery(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=DiscoveredProfile())
    monkeypatch.setattr("nuguard.common.discovery.run_discovery_conversation", mock)
    return mock


def _mock_client_builder(monkeypatch: pytest.MonkeyPatch, client: "_FakeDiscoveryClient") -> None:
    monkeypatch.setattr(
        "nuguard.common.target_client_builder.build_target_app_client", lambda **kw: client
    )


@pytest.mark.asyncio
async def test_cache_hit_skips_live_preflight(monkeypatch):
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/cached/path", chat_payload_key="messages", chat_payload_list=True,
        chat_response_key="reply", endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    _mock_discovery(monkeypatch)
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)

    profile, skip_reason = await _run_pre_scan_discovery(
        sbom, _session_cfg(),
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
    )

    mock_validate.assert_not_awaited()
    assert skip_reason is None
    assert profile is not None


@pytest.mark.asyncio
async def test_cache_miss_runs_live_preflight_and_persists(monkeypatch):
    sbom = _empty_sbom()
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    _mock_discovery(monkeypatch)
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(
            return_value=PreflightOutcome(
                ok=True, rotated_endpoint=("/new/path", "message", False, None),
                endpoint_source="sbom", notes=[],
            )
        ),
    )

    profile, skip_reason = await _run_pre_scan_discovery(
        sbom, _session_cfg(),
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
    )

    assert skip_reason is None
    assert profile is not None
    assert sbom.resolved_chat_endpoint is not None
    assert sbom.resolved_chat_endpoint["chat_path"] == "/new/path"
    assert sbom.resolved_chat_endpoint_fingerprint is not None


@pytest.mark.asyncio
async def test_cache_miss_persists_to_disk_when_sbom_path_set(monkeypatch, tmp_path):
    sbom = _empty_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    _mock_discovery(monkeypatch)
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(return_value=PreflightOutcome(ok=True, notes=[])),
    )

    await _run_pre_scan_discovery(
        sbom, _session_cfg(),
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
        sbom_path=sbom_path,
    )

    import json

    written = json.loads(sbom_path.with_name("app.sbom.enriched.json").read_text())
    assert written["resolved_chat_endpoint"]["chat_path"] == "/chat"


@pytest.mark.asyncio
async def test_preflight_failure_still_returns_skip_reason_on_cache_miss(monkeypatch):
    sbom = _empty_sbom()
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    discover_mock = _mock_discovery(monkeypatch)
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(return_value=PreflightOutcome(ok=False, notes=["no endpoint found"])),
    )

    profile, skip_reason = await _run_pre_scan_discovery(
        sbom, _session_cfg(),
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
    )

    assert profile is None
    assert skip_reason == "no working chat endpoint found during preflight validation"
    discover_mock.assert_not_awaited()
    assert sbom.resolved_chat_endpoint is None


@pytest.mark.asyncio
async def test_explicit_endpoint_ignores_cache_for_different_path(monkeypatch):
    """Config-wins safeguard: a cached resolution for a DIFFERENT path than
    the explicitly configured chat_path must never be used."""
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/some/other/sbom/path", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient(chat_path="/explicitly/configured"))
    _mock_discovery(monkeypatch)
    mock_validate = AsyncMock(return_value=PreflightOutcome(ok=True, notes=[]))
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)

    await _run_pre_scan_discovery(
        sbom, _session_cfg(chat_path="/explicitly/configured"),
        max_turns=2, has_explicit_endpoint=True, preflight_candidates=3,
    )

    mock_validate.assert_awaited_once()
    assert mock_validate.await_args.kwargs["has_explicit_endpoint"] is True


@pytest.mark.asyncio
async def test_explicit_endpoint_matching_cached_path_is_reused(monkeypatch):
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/explicitly/configured", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="config", path_param_values={"id": "conv-2"},
    )
    client = _FakeDiscoveryClient(chat_path="/explicitly/configured")
    _mock_client_builder(monkeypatch, client)
    _mock_discovery(monkeypatch)
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)

    await _run_pre_scan_discovery(
        sbom, _session_cfg(chat_path="/explicitly/configured"),
        max_turns=2, has_explicit_endpoint=True, preflight_candidates=3,
    )

    mock_validate.assert_not_awaited()
    assert client.path_param_values == {"id": "conv-2"}


@pytest.mark.asyncio
async def test_target_auth_mismatch_causes_independent_reresolution(monkeypatch):
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, "http://a-different-target.test", None,
        chat_path="/chat", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    _mock_discovery(monkeypatch)
    mock_validate = AsyncMock(return_value=PreflightOutcome(ok=True, notes=[]))
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)

    await _run_pre_scan_discovery(
        sbom, _session_cfg(),  # non-explicit, target_url=_TARGET_URL
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
    )

    mock_validate.assert_awaited_once()


@pytest.mark.asyncio
async def test_uses_login_flow_aware_auth_config_from_session(monkeypatch):
    """auth_config is pulled from session_cfg.auth_session.auth_config — a
    login_flow identity must be fingerprinted by its configured payload, not
    treated as auth=None (confirming the real AuthSession.auth_config
    plumbing is wired, not bypassed)."""
    from nuguard.common.auth import LoginFlowConfig
    from nuguard.common.endpoint_preflight import endpoint_cache_fingerprint

    login_auth = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", payload={"username": "alice", "password": "a"}),
    )
    sbom = _empty_sbom()
    # Cache entry written against a DIFFERENT (none) identity — must miss
    # for a login_flow-authenticated run even though target/path match.
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/chat", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    _mock_discovery(monkeypatch)
    mock_validate = AsyncMock(return_value=PreflightOutcome(ok=True, notes=[]))
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)

    await _run_pre_scan_discovery(
        sbom, _session_cfg(auth_config=login_auth),
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
    )

    mock_validate.assert_awaited_once()
    # Sanity: confirms the two fingerprints really do differ for this pair.
    assert endpoint_cache_fingerprint(_TARGET_URL, None, "/chat", {}) != endpoint_cache_fingerprint(
        _TARGET_URL, login_auth, "/chat", {}
    )


# ---------------------------------------------------------------------------
# Cross-tool hand-off involving Target Verify specifically (issue #611 Phase
# 3's full test plan: first of {verify, behavior, redteam}, in any order,
# the other two reuse it). The behavior<->redteam pair is already covered,
# with a disk round-trip variant, in
# nuguard/redteam/tests/test_orchestrator_endpoint_cache.py — these two
# complete the set by bringing Target Verify into the hand-off, via a real
# sbom_path (not the same in-memory SBOM object), matching the realistic
# scenario of separate CLI invocations.
# ---------------------------------------------------------------------------


class _FakeToolClient:
    def __init__(self, chat_path: str = "/chat") -> None:
        self.chat_path = chat_path
        self.path_param_values: dict[str, str] = {}

    def set_chat_endpoint(self, path, payload_key, payload_list, response_key=None) -> None:
        self.chat_path = path

    def set_path_param(self, name: str, value: str) -> None:
        self.path_param_values[name] = value


@pytest.mark.asyncio
async def test_verify_target_written_resolution_is_reused_by_redteam_via_disk(monkeypatch, tmp_path):
    """Target Verify validates and persists to a real sbom_path first;
    redteam, reloading a genuinely fresh AiSbomDocument from that file, must
    treat it as a cache hit."""
    from nuguard.redteam.executor.orchestrator import RedteamOrchestrator

    sbom_path = tmp_path / "app.sbom.json"
    sbom = _empty_sbom()
    sbom_path.write_text(sbom.model_dump_json())

    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    _mock_discovery(monkeypatch)
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(
            return_value=PreflightOutcome(
                ok=True, rotated_endpoint=("/discovered/by/verify", "message", False, None),
                endpoint_source="sbom", notes=[],
            )
        ),
    )
    await _run_pre_scan_discovery(
        sbom, _session_cfg(),
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
        sbom_path=sbom_path,
    )
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()  # sanity: the disk write happened

    fresh_sbom = AiSbomDocument.model_validate_json(enriched_path.read_text())
    orchestrator = RedteamOrchestrator(
        sbom=fresh_sbom, target_url=_TARGET_URL, concurrency=1, chat_path="",
    )
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    redteam_client = _FakeToolClient()

    ok, _notes = await orchestrator._ensure_endpoint_preflight(redteam_client, auth_headers=None)

    mock_validate.assert_not_awaited()
    assert ok is True
    assert redteam_client.chat_path == "/discovered/by/verify"


@pytest.mark.asyncio
async def test_behavior_written_resolution_is_reused_by_verify_target_via_disk(monkeypatch, tmp_path):
    """Behavior validates and persists to a real sbom_path first; Target
    Verify, reloading a genuinely fresh AiSbomDocument from that file, must
    treat it as a cache hit and skip its own live preflight."""
    from unittest.mock import MagicMock

    from nuguard.behavior.runner import BehaviorRunner

    sbom_path = tmp_path / "app.sbom.json"
    sbom = _empty_sbom()
    sbom_path.write_text(sbom.model_dump_json())

    cfg = MagicMock()
    cfg.target = _TARGET_URL
    cfg.target_endpoint = ""
    cfg.preflight_candidates = 3
    runner = BehaviorRunner(
        config=cfg, sbom=sbom, policy=None, intent=None, llm_client=None,
        sbom_path=sbom_path, endpoint_explicitly_set=False,
    )
    runner._resolved_target_url = _TARGET_URL
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(
            return_value=PreflightOutcome(
                ok=True, rotated_endpoint=("/discovered/by/behavior", "message", False, None),
                endpoint_source="sbom", notes=[],
            )
        ),
    )
    await runner._ensure_endpoint_preflight(_FakeToolClient())
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()  # sanity: the disk write happened

    fresh_sbom = AiSbomDocument.model_validate_json(enriched_path.read_text())
    _mock_client_builder(monkeypatch, _FakeDiscoveryClient())
    _mock_discovery(monkeypatch)
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)

    profile, skip_reason = await _run_pre_scan_discovery(
        fresh_sbom, _session_cfg(),
        max_turns=2, has_explicit_endpoint=False, preflight_candidates=3,
    )

    mock_validate.assert_not_awaited()
    assert skip_reason is None
    assert profile is not None
