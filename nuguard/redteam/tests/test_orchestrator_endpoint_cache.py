"""Tests for RedteamOrchestrator._ensure_endpoint_preflight's SBOM-backed
chat-endpoint resolution cache (issue #611 Phase 3).

Calls _ensure_endpoint_preflight directly against a constructed orchestrator
instance (bypassing run()'s full bootstrap/scenario pipeline, already
exercised by test_orchestrator_discovery_cache.py for the sibling
discovered_profile cache) — this is the exact method Phase 3 modified, so
driving it directly gives deterministic, fast coverage of:
  - a cache hit (matching resolved_chat_endpoint_fingerprint) skips
    validate_and_rotate_chat_endpoint entirely and binds the cached
    resolution onto the client
  - a cache miss (mismatched fingerprint) runs live preflight and persists
    the fresh result (in-memory; to disk when sbom_path is set)
  - the config-wins safeguard: an explicitly configured endpoint ignores a
    cached resolution for a DIFFERENT path and always re-validates live
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nuguard.common.auth import AuthConfig
from nuguard.common.endpoint_preflight import PreflightOutcome, persist_endpoint_resolution
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.sbom.models import AiSbomDocument

_TARGET_URL = "http://target.test"


class _FakeClient:
    def __init__(self, chat_path: str = "/chat") -> None:
        self.chat_path = chat_path
        self.path_param_values: dict[str, str] = {}
        self._chat_payload_key = "message"
        self._chat_payload_list = False
        self._chat_response_key: str | None = None

    def set_chat_endpoint(self, path, payload_key, payload_list, response_key=None) -> None:
        self.chat_path = path
        self._chat_payload_key = payload_key
        self._chat_payload_list = payload_list
        self._chat_response_key = response_key

    def set_path_param(self, name: str, value: str) -> None:
        self.path_param_values[name] = value


def _empty_sbom(**kwargs) -> AiSbomDocument:
    return AiSbomDocument(target="unit-test", nodes=[], edges=[], **kwargs)


def _make_orchestrator(
    sbom: AiSbomDocument,
    *,
    chat_path: str = "",
    auth_config: "AuthConfig | None" = None,
    sbom_path=None,
) -> RedteamOrchestrator:
    return RedteamOrchestrator(
        sbom=sbom,
        target_url=_TARGET_URL,
        sbom_path=sbom_path,
        concurrency=1,
        chat_path=chat_path,
        auth_config=auth_config,
    )


@pytest.mark.asyncio
async def test_cache_hit_skips_live_preflight(monkeypatch):
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/cached/path", chat_payload_key="messages", chat_payload_list=True,
        chat_response_key="reply", endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    orchestrator = _make_orchestrator(sbom, chat_path="")  # non-explicit
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    client = _FakeClient()

    ok, notes = await orchestrator._ensure_endpoint_preflight(client, auth_headers=None)

    mock_validate.assert_not_awaited()
    assert ok is True
    assert client.chat_path == "/cached/path"
    assert client.path_param_values == {"id": "conv-1"}
    assert orchestrator._chat_path == "/cached/path"


@pytest.mark.asyncio
async def test_cache_miss_runs_live_preflight_and_persists(monkeypatch):
    sbom = _empty_sbom()
    orchestrator = _make_orchestrator(sbom, chat_path="")
    mock_validate = AsyncMock(
        return_value=PreflightOutcome(
            ok=True,
            rotated_endpoint=("/new/path", "message", False, None),
            endpoint_source="sbom",
            notes=[],
        )
    )
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    client = _FakeClient()

    ok, _notes = await orchestrator._ensure_endpoint_preflight(client, auth_headers=None)

    mock_validate.assert_awaited_once()
    assert ok is True
    assert orchestrator._chat_path == "/new/path"
    assert sbom.resolved_chat_endpoint is not None
    assert sbom.resolved_chat_endpoint["chat_path"] == "/new/path"
    assert sbom.resolved_chat_endpoint_fingerprint is not None


@pytest.mark.asyncio
async def test_cache_miss_persists_to_disk_when_sbom_path_set(monkeypatch, tmp_path):
    sbom = _empty_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    orchestrator = _make_orchestrator(sbom, chat_path="", sbom_path=sbom_path)
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(return_value=PreflightOutcome(ok=True, notes=[])),
    )
    mock_persist = MagicMock(return_value=sbom_path.with_name("app.sbom.enriched.json"))
    monkeypatch.setattr(
        "nuguard.common.auto_sbom_enricher.persist_endpoint_resolution_sbom", mock_persist
    )
    client = _FakeClient(chat_path="/chat")

    await orchestrator._ensure_endpoint_preflight(client, auth_headers=None)

    mock_persist.assert_called_once_with(sbom, sbom_path)


@pytest.mark.asyncio
async def test_explicit_endpoint_ignores_cache_for_different_path(monkeypatch):
    """The config-wins safeguard: a cache entry resolved to a different path
    (e.g. from an earlier non-explicit SBOM-rotated run) must never override
    the currently-configured explicit endpoint — live preflight always runs
    on the explicit path instead."""
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/some/other/sbom/path", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    orchestrator = _make_orchestrator(sbom, chat_path="/explicitly/configured")
    assert orchestrator._chat_path_source == "config"
    mock_validate = AsyncMock(return_value=PreflightOutcome(ok=True, notes=[]))
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    client = _FakeClient(chat_path="/explicitly/configured")

    await orchestrator._ensure_endpoint_preflight(client, auth_headers=None)

    mock_validate.assert_awaited_once()
    _call_kwargs = mock_validate.await_args.kwargs
    assert _call_kwargs["has_explicit_endpoint"] is True


@pytest.mark.asyncio
async def test_explicit_endpoint_matching_cached_path_is_reused(monkeypatch):
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/explicitly/configured", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="config", path_param_values={"id": "conv-2"},
    )
    orchestrator = _make_orchestrator(sbom, chat_path="/explicitly/configured")
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    client = _FakeClient(chat_path="/explicitly/configured")

    ok, _notes = await orchestrator._ensure_endpoint_preflight(client, auth_headers=None)

    mock_validate.assert_not_awaited()
    assert ok is True
    assert client.path_param_values == {"id": "conv-2"}


@pytest.mark.asyncio
async def test_target_auth_mismatch_causes_independent_reresolution(monkeypatch):
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, "http://a-different-target.test", None,
        chat_path="/chat", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    orchestrator = _make_orchestrator(sbom, chat_path="")  # non-explicit, target_url=_TARGET_URL
    mock_validate = AsyncMock(return_value=PreflightOutcome(ok=True, notes=[]))
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    client = _FakeClient()

    await orchestrator._ensure_endpoint_preflight(client, auth_headers=None)

    mock_validate.assert_awaited_once()


# ---------------------------------------------------------------------------
# Cross-tool hand-off (issue #611 Phase 3's actual goal: whoever validates
# the endpoint first, the other tool reuses it — run order must not matter).
# Mirrors test_orchestrator_discovery_cache.py's equivalent pair for the
# sibling discovered_profile cache.
# ---------------------------------------------------------------------------


def _make_behavior_runner(sbom: AiSbomDocument, *, explicit: bool):
    from nuguard.behavior.runner import BehaviorRunner

    cfg = MagicMock()
    cfg.target = _TARGET_URL
    cfg.target_endpoint = ""
    cfg.preflight_candidates = 3
    runner = BehaviorRunner(
        config=cfg, sbom=sbom, policy=None, intent=None, llm_client=None,
        endpoint_explicitly_set=explicit,
    )
    runner._resolved_target_url = _TARGET_URL
    return runner


@pytest.mark.asyncio
async def test_orchestrator_written_resolution_is_reused_by_behavior(monkeypatch):
    """Redteam validates the endpoint first; a later behavior run against the
    same in-memory SBOM (no sbom_path) must treat it as a cache hit."""
    sbom = _empty_sbom()
    orchestrator = _make_orchestrator(sbom, chat_path="")
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(
            return_value=PreflightOutcome(
                ok=True, rotated_endpoint=("/discovered/by/redteam", "message", False, None),
                endpoint_source="sbom", notes=[],
            )
        ),
    )
    await orchestrator._ensure_endpoint_preflight(_FakeClient(), auth_headers=None)
    assert sbom.resolved_chat_endpoint is not None  # sanity: the write happened

    runner = _make_behavior_runner(sbom, explicit=False)
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    behavior_client = _FakeClient()

    ok = await runner._ensure_endpoint_preflight(behavior_client)

    mock_validate.assert_not_awaited()
    assert ok is True
    assert behavior_client.chat_path == "/discovered/by/redteam"


@pytest.mark.asyncio
async def test_behavior_written_resolution_is_reused_by_orchestrator(monkeypatch):
    """Behavior validates the endpoint first; a later redteam run against the
    same in-memory SBOM (no sbom_path) must treat it as a cache hit."""
    sbom = _empty_sbom()
    runner = _make_behavior_runner(sbom, explicit=False)
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(
            return_value=PreflightOutcome(
                ok=True, rotated_endpoint=("/discovered/by/behavior", "message", False, None),
                endpoint_source="sbom", notes=[],
            )
        ),
    )
    await runner._ensure_endpoint_preflight(_FakeClient())
    assert sbom.resolved_chat_endpoint is not None  # sanity: the write happened

    orchestrator = _make_orchestrator(sbom, chat_path="")
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    redteam_client = _FakeClient()

    ok, _notes = await orchestrator._ensure_endpoint_preflight(redteam_client, auth_headers=None)

    mock_validate.assert_not_awaited()
    assert ok is True
    assert redteam_client.chat_path == "/discovered/by/behavior"


@pytest.mark.asyncio
async def test_non_explicit_hit_does_not_inherit_config_source_label(monkeypatch):
    """Regression: a cache entry written by an earlier EXPLICIT run must not
    make a later NON-explicit run falsely report endpoint_source="config" —
    that run never configured anything itself, so the label would be a lie
    in reports/traces (see endpoint-resolution-precedence-plan.md's
    requirement that source labeling stay accurate)."""
    sbom = _empty_sbom()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/chat", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="config", path_param_values={},
    )
    orchestrator = _make_orchestrator(sbom, chat_path="")  # non-explicit
    before = orchestrator._chat_path_source
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", AsyncMock()
    )
    client = _FakeClient(chat_path="/chat")

    await orchestrator._ensure_endpoint_preflight(client, auth_headers=None)

    assert orchestrator._chat_path_source != "config"
    assert orchestrator._chat_path_source == before


# ---------------------------------------------------------------------------
# Cross-tool hand-off via DISK (not the same in-memory SBOM object) — the
# realistic scenario: two separate CLI invocations, possibly different
# processes, sharing an on-disk enriched SBOM. Mirrors the disk round-trip
# pattern in nuguard/common/tests/test_endpoint_preflight_cache.py, but
# across two actually-different tools instead of the same function twice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_written_resolution_survives_disk_round_trip_for_behavior(
    monkeypatch, tmp_path
):
    """Redteam validates and persists to a real sbom_path; behavior, in a
    later run reloading a genuinely fresh AiSbomDocument from that file (not
    the same in-memory object redteam wrote to), must get a cache hit."""
    sbom_path = tmp_path / "app.sbom.json"
    sbom = _empty_sbom()
    sbom_path.write_text(sbom.model_dump_json())

    orchestrator = _make_orchestrator(sbom, chat_path="", sbom_path=sbom_path)
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(
            return_value=PreflightOutcome(
                ok=True, rotated_endpoint=("/discovered/by/redteam", "message", False, None),
                endpoint_source="sbom", notes=[],
            )
        ),
    )
    await orchestrator._ensure_endpoint_preflight(_FakeClient(), auth_headers=None)
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()  # sanity: the disk write happened

    fresh_sbom = AiSbomDocument.model_validate_json(enriched_path.read_text())
    runner = _make_behavior_runner(fresh_sbom, explicit=False)
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    behavior_client = _FakeClient()

    ok = await runner._ensure_endpoint_preflight(behavior_client)

    mock_validate.assert_not_awaited()
    assert ok is True
    assert behavior_client.chat_path == "/discovered/by/redteam"


@pytest.mark.asyncio
async def test_behavior_written_resolution_survives_disk_round_trip_for_orchestrator(
    monkeypatch, tmp_path
):
    """Behavior validates and persists to a real sbom_path; redteam, in a
    later run reloading a genuinely fresh AiSbomDocument from that file,
    must get a cache hit."""
    sbom_path = tmp_path / "app.sbom.json"
    sbom = _empty_sbom()
    sbom_path.write_text(sbom.model_dump_json())

    runner = _make_behavior_runner(sbom, explicit=False)
    runner._sbom_path = sbom_path
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint",
        AsyncMock(
            return_value=PreflightOutcome(
                ok=True, rotated_endpoint=("/discovered/by/behavior", "message", False, None),
                endpoint_source="sbom", notes=[],
            )
        ),
    )
    await runner._ensure_endpoint_preflight(_FakeClient())
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()  # sanity: the disk write happened

    fresh_sbom = AiSbomDocument.model_validate_json(enriched_path.read_text())
    orchestrator = _make_orchestrator(fresh_sbom, chat_path="")
    mock_validate = AsyncMock()
    monkeypatch.setattr("nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", mock_validate)
    redteam_client = _FakeClient()

    ok, _notes = await orchestrator._ensure_endpoint_preflight(redteam_client, auth_headers=None)

    mock_validate.assert_not_awaited()
    assert ok is True
    assert redteam_client.chat_path == "/discovered/by/behavior"
