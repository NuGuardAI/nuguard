"""Tests for save_controls/load_controls round-tripping provenance fields."""

from __future__ import annotations

from pathlib import Path

from nuguard.models.policy import PolicyControl, PolicyOrigin
from nuguard.policy.loader import load_controls, save_controls
from nuguard.sbom.models import SourceLocation


def test_save_and_load_controls_round_trips_origin_and_evidence(tmp_path: Path) -> None:
    controls = [
        PolicyControl(
            id="CTRL-001",
            section="restricted_actions",
            description="Wire transfers",
            control_type="action_restriction",
            severity="high",
            origin=PolicyOrigin.POLICY_DOCUMENT.value,
            evidence=[SourceLocation(path="cognitive_policy.md", line=10)],
        ),
        PolicyControl(
            id="CTRL-002",
            section="hitl_triggers",
            description="Default HITL rule",
            control_type="hitl",
            severity="high",
            origin=PolicyOrigin.NUGUARD_BEST_PRACTICE.value,
            evidence=[],
        ),
    ]
    dest = tmp_path / "cognitive_policy.json"
    save_controls(controls, dest)

    loaded = load_controls(dest)
    assert len(loaded) == 2
    assert loaded[0].origin == PolicyOrigin.POLICY_DOCUMENT.value
    assert loaded[0].evidence == [SourceLocation(path="cognitive_policy.md", line=10)]
    assert loaded[1].origin == PolicyOrigin.NUGUARD_BEST_PRACTICE.value
    assert loaded[1].evidence == []


def test_save_controls_json_contains_origin_and_evidence_keys(tmp_path: Path) -> None:
    import json

    controls = [
        PolicyControl(
            id="CTRL-001",
            section="restricted_actions",
            description="Wire transfers",
            control_type="action_restriction",
            evidence=[SourceLocation(path="p.md", line=3)],
        ),
    ]
    dest = tmp_path / "out.json"
    save_controls(controls, dest)

    raw = json.loads(dest.read_text())
    assert raw[0]["origin"] == "policy_document"
    assert raw[0]["evidence"] == [{"path": "p.md", "line": 3}]
