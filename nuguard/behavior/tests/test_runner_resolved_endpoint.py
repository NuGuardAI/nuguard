"""Regression: the shared target-session resolution in BehaviorRunner must keep
an endpoint BehaviorAnalyzer already live-confirmed instead of re-resolving it
(v0.9.13 dropped /extract for kscope and switched to a rejecting /chat), and
must judge payload-key explicitness by what the user configured, not by fields
the analyzer folded into the config via model_copy.
"""
from __future__ import annotations

from typing import Any

import pytest

from nuguard.behavior.runner import BehaviorRunner
from nuguard.behavior.tests.test_runner_bootstrap_abort import _fake_session_cfg
from nuguard.config import BehaviorConfig
from nuguard.models.health_report import TargetHealthReport


def _analyzer_resolved_config() -> BehaviorConfig:
    cfg = BehaviorConfig(target="http://localhost:8080")
    return cfg.model_copy(update={"target_endpoint": "/extract", "chat_payload_key": "text"})


def _capture_resolution(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake(**kwargs: Any):
        captured.update(kwargs)
        report = TargetHealthReport(target_url="http://localhost:8080", endpoint="/extract", run_id="r")
        return _fake_session_cfg(), report

    monkeypatch.setattr("nuguard.common.session_resolver.resolve_target_session", _fake)
    return captured


@pytest.mark.asyncio
async def test_live_confirmed_endpoint_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_resolution(monkeypatch)
    runner = BehaviorRunner(
        config=_analyzer_resolved_config(),
        endpoint_explicitly_set=False,
        resolved_endpoint_source="probe",
        user_config_fields=frozenset({"target"}),
    )

    await runner._resolve_target_session_once()

    assert captured["chat_path"] == "/extract"
    assert captured["endpoint_explicit"] is True
    assert captured["chat_payload_key"] == "text"
    assert captured["payload_key_explicit"] is True
    assert captured["endpoint_source_hint"] == "probe"
    # Rotation still treats the endpoint as auto-discovered, not user-set.
    assert runner._endpoint_is_explicit() is False


@pytest.mark.asyncio
async def test_unconfirmed_endpoint_uses_user_fields_for_payload_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_resolution(monkeypatch)
    runner = BehaviorRunner(
        config=_analyzer_resolved_config(),
        endpoint_explicitly_set=False,
        resolved_endpoint_source=None,
        user_config_fields=frozenset({"target"}),
    )

    await runner._resolve_target_session_once()

    assert captured["endpoint_explicit"] is False
    assert captured["payload_key_explicit"] is False
    assert captured["endpoint_source_hint"] is None
