"""Tests for nuguard.common.run_checkpoint (issue #508)."""
from __future__ import annotations

import json

import pytest

from nuguard.common.run_checkpoint import (
    CheckpointMismatchError,
    RunCheckpoint,
    attack_scenario_signature,
    behavior_scenario_obj_signature,
    fingerprint,
    redteam_scenario_signature,
    scenario_record_signature,
    validate_fingerprint,
)


class _FakeSbom:
    def model_dump_json(self) -> str:
        return json.dumps({"nodes": ["a", "b"]})


class _FakePolicy:
    def model_dump_json(self) -> str:
        return json.dumps({"allowed_topics": ["billing"]})


def test_fingerprint_is_stable_for_equivalent_inputs() -> None:
    a = fingerprint(_FakeSbom(), _FakePolicy())
    b = fingerprint(_FakeSbom(), _FakePolicy())
    assert a == b
    assert len(a) == 16


def test_fingerprint_changes_with_policy() -> None:
    class _OtherPolicy:
        def model_dump_json(self) -> str:
            return json.dumps({"allowed_topics": ["refunds"]})

    assert fingerprint(_FakeSbom(), _FakePolicy()) != fingerprint(_FakeSbom(), _OtherPolicy())


def test_fingerprint_handles_none_policy() -> None:
    # Must not raise even when policy is absent.
    assert fingerprint(_FakeSbom(), None)


def test_save_then_load_round_trip(tmp_path) -> None:
    ckpt = RunCheckpoint(tmp_path, "redteam")
    path = ckpt.path_for("abc123")
    ckpt.save(path, {"cache_key": "abc123", "scenario_records": [{"title": "x"}]})

    loaded = ckpt.load(path)
    assert loaded is not None
    assert loaded["cache_key"] == "abc123"
    assert loaded["scenario_records"] == [{"title": "x"}]
    assert loaded["run_kind"] == "redteam"
    assert loaded["checkpoint_version"] == 1
    assert "updated_at" in loaded and "created_at" in loaded


def test_save_creates_output_dir(tmp_path) -> None:
    output_dir = tmp_path / "nested" / "dir"
    ckpt = RunCheckpoint(output_dir, "behavior")
    path = ckpt.path_for("k1")
    ckpt.save(path, {"cache_key": "k1"})
    assert path.exists()
    assert path.parent == output_dir


def test_load_missing_file_returns_none(tmp_path) -> None:
    ckpt = RunCheckpoint(tmp_path, "redteam")
    assert ckpt.load(tmp_path / "nonexistent.json") is None


def test_load_malformed_json_returns_none(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    ckpt = RunCheckpoint(tmp_path, "redteam")
    assert ckpt.load(path) is None


def test_load_rejects_wrong_run_kind(tmp_path) -> None:
    ckpt = RunCheckpoint(tmp_path, "redteam")
    path = ckpt.path_for("k1")
    ckpt.save(path, {"cache_key": "k1"})

    behavior_ckpt = RunCheckpoint(tmp_path, "behavior")
    assert behavior_ckpt.load(path) is None


def test_delete_is_best_effort_on_missing_file(tmp_path) -> None:
    ckpt = RunCheckpoint(tmp_path, "redteam")
    # Should not raise even though the file was never created.
    ckpt.delete(tmp_path / "never-existed.json")


def test_delete_removes_existing_file(tmp_path) -> None:
    ckpt = RunCheckpoint(tmp_path, "redteam")
    path = ckpt.path_for("k1")
    ckpt.save(path, {"cache_key": "k1"})
    assert path.exists()
    ckpt.delete(path)
    assert not path.exists()


def test_save_survives_unserializable_payload_value(tmp_path) -> None:
    """A write must never crash the run it is protecting."""
    ckpt = RunCheckpoint(tmp_path, "redteam")
    path = ckpt.path_for("k1")

    class _Unserializable:
        pass

    # default=str in json.dump makes this survive rather than raise.
    ckpt.save(path, {"cache_key": "k1", "weird": _Unserializable()})
    assert path.exists()


def test_validate_fingerprint_passes_for_matching_inputs() -> None:
    checkpoint = {"cache_key": fingerprint(_FakeSbom(), _FakePolicy())}
    validate_fingerprint(checkpoint, sbom=_FakeSbom(), policy=_FakePolicy())


def test_validate_fingerprint_raises_on_mismatch() -> None:
    checkpoint = {"cache_key": "deadbeefdeadbeef"}
    with pytest.raises(CheckpointMismatchError):
        validate_fingerprint(checkpoint, sbom=_FakeSbom(), policy=_FakePolicy())


class _FakeGoalType:
    value = "DATA_EXFILTRATION"


class _FakeScenarioType:
    value = "PII_EXTRACTION"


class _FakeAttackScenario:
    def __init__(self, title: str, catalog_id: str = "") -> None:
        self.goal_type = _FakeGoalType()
        self.scenario_type = _FakeScenarioType()
        self.title = title
        self.catalog_id = catalog_id


class _FakeScenarioRecord:
    def __init__(self, title: str, catalog_id: str | None = None) -> None:
        self.goal_type = "DATA_EXFILTRATION"
        self.scenario_type = "PII_EXTRACTION"
        self.title = title
        self.catalog_id = catalog_id


def test_redteam_signature_survives_uuid_regeneration() -> None:
    """The whole point: scenario_id (a fresh uuid4 every generate() call) plays no part."""
    s1 = _FakeAttackScenario(title="Extract customer PII")
    s2 = _FakeAttackScenario(title="Extract customer PII")  # simulates a re-generated scenario
    assert attack_scenario_signature(s1) == attack_scenario_signature(s2)


def test_redteam_signature_matches_between_scenario_and_record() -> None:
    scenario = _FakeAttackScenario(title="Extract customer PII")
    record = _FakeScenarioRecord(title="Extract customer PII")
    assert attack_scenario_signature(scenario) == scenario_record_signature(record)


def test_redteam_signature_prefers_catalog_id_over_title() -> None:
    scenario = _FakeAttackScenario(title="Templated title A", catalog_id="C01")
    record_same_catalog_different_title = _FakeScenarioRecord(title="Templated title B", catalog_id="C01")
    assert attack_scenario_signature(scenario) == scenario_record_signature(record_same_catalog_different_title)


def test_redteam_signature_distinguishes_different_titles_without_catalog_id() -> None:
    a = _FakeAttackScenario(title="Title A")
    b = _FakeAttackScenario(title="Title B")
    assert attack_scenario_signature(a) != attack_scenario_signature(b)


def test_redteam_scenario_signature_function_directly() -> None:
    assert (
        redteam_scenario_signature("GOAL", "TYPE", "Title")
        == "GOAL|TYPE|Title"
    )
    assert (
        redteam_scenario_signature("GOAL", "TYPE", "Title", catalog_id="C01")
        == "GOAL|TYPE|catalog:C01"
    )


class _FakeBehaviorScenarioType:
    value = "guardrail_probe"


class _FakeBehaviorScenario:
    def __init__(self, name: str) -> None:
        self.scenario_type = _FakeBehaviorScenarioType()
        self.name = name


def test_behavior_signature_survives_uuid_regeneration() -> None:
    a = _FakeBehaviorScenario(name="HITL guardrail probe")
    b = _FakeBehaviorScenario(name="HITL guardrail probe")
    assert behavior_scenario_obj_signature(a) == behavior_scenario_obj_signature(b)


def test_behavior_signature_distinguishes_names() -> None:
    a = _FakeBehaviorScenario(name="Probe A")
    b = _FakeBehaviorScenario(name="Probe B")
    assert behavior_scenario_obj_signature(a) != behavior_scenario_obj_signature(b)
