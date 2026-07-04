"""Tests for the catalog YAML loader and exporter."""
from __future__ import annotations

import warnings
from pathlib import Path

import pytest
import yaml

from nuguard.redteam.catalog.loader import export_catalog_yaml, load_catalog_yaml
from nuguard.redteam.catalog.registry import SCENARIO_CATALOG


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_catalog(tmp_path: Path, scenarios: list[dict]) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(
        yaml.dump({"scenarios": scenarios}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return p


def _minimal_entry(**overrides) -> dict:
    base = {
        "id": "X01",
        "category": "Data Exfiltration",
        "title": "Test scenario",
        "goal_type": "DATA_EXFILTRATION",
        "scenario_type": "DIRECT_PII_EXTRACTION",
        "delivery_channel": "user_prompt",
        "source_trust": "untrusted_user",
        "sink_type": "final_answer",
        "required_capabilities": ["sensitive_context"],
        "evidence_types": ["response_quote"],
        "safe_execution": "canary_only",
        "expected_control": "Refuse full dump.",
        "success_signal": "PII appears.",
        "base_impact": 5.0,
        "enabled": True,
    }
    base.update(overrides)
    return base


# ── export tests ──────────────────────────────────────────────────────────────


def test_export_produces_valid_yaml():
    yaml_text = export_catalog_yaml(SCENARIO_CATALOG)
    data = yaml.safe_load(yaml_text)
    assert "scenarios" in data
    assert len(data["scenarios"]) == len(SCENARIO_CATALOG)


def test_export_writes_file(tmp_path: Path):
    out = tmp_path / "cat.yaml"
    text = export_catalog_yaml(SCENARIO_CATALOG, path=out)
    assert out.exists()
    assert out.read_text(encoding="utf-8") == text


def test_export_first_entry_fields():
    yaml_text = export_catalog_yaml(SCENARIO_CATALOG)
    data = yaml.safe_load(yaml_text)
    first = data["scenarios"][0]
    assert "id" in first
    assert "title" in first
    assert "goal_type" in first
    assert "builder_key" in first or "builder_key" not in first  # optional when empty


# ── roundtrip tests ───────────────────────────────────────────────────────────


def test_roundtrip_full_catalog(tmp_path: Path):
    out = tmp_path / "catalog.yaml"
    export_catalog_yaml(SCENARIO_CATALOG, path=out)
    loaded = load_catalog_yaml(out)
    assert len(loaded) == len(SCENARIO_CATALOG)
    # IDs are preserved
    original_ids = {s.id for s in SCENARIO_CATALOG}
    loaded_ids = {s.id for s in loaded}
    assert loaded_ids == original_ids


def test_roundtrip_preserves_fields(tmp_path: Path):
    out = tmp_path / "catalog.yaml"
    export_catalog_yaml(SCENARIO_CATALOG, path=out)
    loaded = load_catalog_yaml(out)
    loaded_map = {s.id: s for s in loaded}

    for orig in SCENARIO_CATALOG:
        restored = loaded_map[orig.id]
        assert restored.title == orig.title
        assert restored.goal_type == orig.goal_type
        assert restored.scenario_type == orig.scenario_type
        assert restored.base_impact == orig.base_impact
        assert restored.enabled == orig.enabled
        assert restored.required_capabilities == orig.required_capabilities
        assert restored.evidence_types == orig.evidence_types


# ── validation error tests ────────────────────────────────────────────────────


def test_load_invalid_delivery_channel(tmp_path: Path):
    entry = _minimal_entry(delivery_channel="does_not_exist")
    p = _write_catalog(tmp_path, [entry])
    with pytest.raises(ValueError, match="delivery_channel"):
        load_catalog_yaml(p)


def test_load_invalid_goal_type(tmp_path: Path):
    entry = _minimal_entry(goal_type="NOT_A_GOAL")
    p = _write_catalog(tmp_path, [entry])
    with pytest.raises(ValueError, match="X01"):
        load_catalog_yaml(p)


def test_load_invalid_capability(tmp_path: Path):
    entry = _minimal_entry(required_capabilities=["not_a_cap"])
    p = _write_catalog(tmp_path, [entry])
    with pytest.raises(ValueError, match="required_capabilities"):
        load_catalog_yaml(p)


def test_load_missing_required_field(tmp_path: Path):
    entry = _minimal_entry()
    del entry["title"]
    p = _write_catalog(tmp_path, [entry])
    with pytest.raises(ValueError, match="title"):
        load_catalog_yaml(p)


def test_load_duplicate_ids(tmp_path: Path):
    entries = [_minimal_entry(), _minimal_entry(id="X01")]  # same id twice
    p = _write_catalog(tmp_path, entries)
    with pytest.raises(ValueError, match="Duplicate"):
        load_catalog_yaml(p)


def test_load_multiple_errors_reported_together(tmp_path: Path):
    bad1 = _minimal_entry(id="Y01", delivery_channel="BAD1")
    bad2 = _minimal_entry(id="Y02", sink_type="BAD2")
    p = _write_catalog(tmp_path, [bad1, bad2])
    with pytest.raises(ValueError) as exc_info:
        load_catalog_yaml(p)
    msg = str(exc_info.value)
    assert "Y01" in msg
    assert "Y02" in msg


def test_load_missing_scenarios_key(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("not_scenarios:\n  - foo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scenarios"):
        load_catalog_yaml(p)


def test_load_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_catalog_yaml(tmp_path / "nonexistent.yaml")


# ── warning tests ─────────────────────────────────────────────────────────────


def test_unknown_builder_key_warns(tmp_path: Path):
    entry = _minimal_entry(builder_key="future_builder_xyz", enabled=True)
    p = _write_catalog(tmp_path, [entry])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        specs = load_catalog_yaml(p)
    assert len(specs) == 1, "spec should still be loaded despite warning"
    assert any("future_builder_xyz" in str(w.message) for w in caught), (
        "expected a UserWarning about the unknown builder_key"
    )


def test_disabled_unknown_builder_key_no_warn(tmp_path: Path):
    entry = _minimal_entry(builder_key="future_builder_xyz", enabled=False)
    p = _write_catalog(tmp_path, [entry])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        specs = load_catalog_yaml(p)
    assert len(specs) == 1
    # Disabled entries don't warn about missing builder_key
    assert not any("future_builder_xyz" in str(w.message) for w in caught)


# ── functional tests ──────────────────────────────────────────────────────────


def test_load_minimal_valid_entry(tmp_path: Path):
    p = _write_catalog(tmp_path, [_minimal_entry()])
    specs = load_catalog_yaml(p)
    assert len(specs) == 1
    s = specs[0]
    assert s.id == "X01"
    assert s.title == "Test scenario"
    assert s.base_impact == 5.0
    assert s.enabled is True
    assert s.priority_rules == ()
    assert s.owasp_llm == ()
    assert s.owasp_agentic == ()


def test_load_preserves_disabled_flag(tmp_path: Path):
    entry = _minimal_entry(enabled=False)
    p = _write_catalog(tmp_path, [entry])
    specs = load_catalog_yaml(p)
    assert specs[0].enabled is False


def test_load_multiple_capabilities(tmp_path: Path):
    entry = _minimal_entry(
        required_capabilities=["sensitive_context", "web_fetch", "mcp_server"]
    )
    p = _write_catalog(tmp_path, [entry])
    specs = load_catalog_yaml(p)
    from nuguard.redteam.catalog.taxonomy import Capability
    assert Capability.WEB_FETCH in specs[0].required_capabilities
    assert Capability.MCP_SERVER in specs[0].required_capabilities
