"""Snapshot tests for the stable-ID red-team scenario catalog.

These tests enforce the structural invariants of the catalog defined in
``docs/redteam-design.md`` and implemented in
``nuguard/redteam/catalog/``:

1. All 130 stable IDs are present and unique.
2. Each series (D/C/T/A/I/M/P/G/J/E/B/K/R/O/H/N/S/V/W) is contiguous with no gaps.
3. Every ``builder_key`` resolves in ``BUILDER_FACTORIES``.
4. Every field value is a valid enum member (no typos).
5. Taxonomy invariants hold (required_capabilities non-empty, evidence_types
   non-empty, etc.).
6. The AttackScenario model accepts the new catalog fields without error.
7. OWASP LLM 2025 and Agentic 2026 coverage is comprehensive.
"""
from __future__ import annotations

import collections
from typing import get_args

import pytest

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.catalog.builders import BUILDER_FACTORIES
from nuguard.redteam.catalog.registry import CATALOG_BY_ID, SCENARIO_CATALOG
from nuguard.redteam.catalog.taxonomy import (
    Capability,
    DeliveryChannel,
    EvidenceType,
    SafeExecution,
    ScenarioCategory,
    SinkType,
    SourceTrust,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

EXPECTED_ID_SERIES: dict[str, int] = {
    "D": 8,
    "C": 8,
    "T": 8,
    "A": 8,
    "I": 8,
    "M": 8,
    "P": 6,
    "G": 6,
    "J": 6,
    "E": 7,
    "B": 6,
    "K": 6,
    "R": 8,
    "O": 6,
    "H": 5,
    "N": 6,
    "S": 8,
    "V": 7,
    "W": 5,
}
EXPECTED_TOTAL = sum(EXPECTED_ID_SERIES.values())  # 130


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_total_spec_count() -> None:
    """Catalog must contain exactly 130 specs."""
    assert len(SCENARIO_CATALOG) == EXPECTED_TOTAL, (
        f"Expected {EXPECTED_TOTAL} specs, got {len(SCENARIO_CATALOG)}"
    )


def test_all_ids_unique() -> None:
    """No two specs share the same stable ID."""
    ids = [s.id for s in SCENARIO_CATALOG]
    duplicates = {k: v for k, v in collections.Counter(ids).items() if v > 1}
    assert not duplicates, f"Duplicate catalog IDs: {duplicates}"


def test_catalog_by_id_matches_catalog() -> None:
    """CATALOG_BY_ID has exactly the same entries as SCENARIO_CATALOG."""
    assert set(CATALOG_BY_ID.keys()) == {s.id for s in SCENARIO_CATALOG}
    for spec in SCENARIO_CATALOG:
        assert CATALOG_BY_ID[spec.id] is spec


def test_series_contiguous() -> None:
    """Each series prefix must have the expected number of contiguous entries."""
    by_series: dict[str, list[int]] = collections.defaultdict(list)
    for s in SCENARIO_CATALOG:
        prefix = s.id[0]
        num = int(s.id[1:])
        by_series[prefix].append(num)

    for prefix, expected_count in EXPECTED_ID_SERIES.items():
        nums = sorted(by_series[prefix])
        assert nums == list(range(1, expected_count + 1)), (
            f"Series {prefix!r}: expected [1..{expected_count}], got {nums}"
        )

    extra = set(by_series.keys()) - set(EXPECTED_ID_SERIES.keys())
    assert not extra, f"Unexpected series prefixes: {extra}"


def test_all_builder_keys_resolve() -> None:
    """Every spec's resolved builder_key must exist in BUILDER_FACTORIES."""
    unresolved = [
        (s.id, s.resolved_builder_key())
        for s in SCENARIO_CATALOG
        if s.resolved_builder_key() not in BUILDER_FACTORIES
    ]
    assert not unresolved, (
        f"Specs with unresolved builder keys: {unresolved}"
    )


def test_valid_goal_types() -> None:
    valid = {m.value for m in GoalType}
    invalid = [
        (s.id, s.goal_type)
        for s in SCENARIO_CATALOG
        if s.goal_type.value not in valid
    ]
    assert not invalid, f"Invalid goal_type values: {invalid}"


def test_valid_scenario_types() -> None:
    valid = {m.value for m in ScenarioType}
    invalid = [
        (s.id, s.scenario_type)
        for s in SCENARIO_CATALOG
        if s.scenario_type.value not in valid
    ]
    assert not invalid, f"Invalid scenario_type values: {invalid}"


def test_valid_delivery_channels() -> None:
    valid = {m.value for m in DeliveryChannel}
    invalid = [
        (s.id, s.delivery_channel)
        for s in SCENARIO_CATALOG
        if s.delivery_channel.value not in valid
    ]
    assert not invalid, f"Invalid delivery_channel values: {invalid}"


def test_valid_source_trust() -> None:
    valid = {m.value for m in SourceTrust}
    invalid = [
        (s.id, s.source_trust)
        for s in SCENARIO_CATALOG
        if s.source_trust.value not in valid
    ]
    assert not invalid, f"Invalid source_trust values: {invalid}"


def test_valid_sink_types() -> None:
    valid = {m.value for m in SinkType}
    invalid = [
        (s.id, s.sink_type)
        for s in SCENARIO_CATALOG
        if s.sink_type.value not in valid
    ]
    assert not invalid, f"Invalid sink_type values: {invalid}"


def test_valid_safe_execution() -> None:
    valid = {m.value for m in SafeExecution}
    invalid = [
        (s.id, s.safe_execution)
        for s in SCENARIO_CATALOG
        if s.safe_execution.value not in valid
    ]
    assert not invalid, f"Invalid safe_execution values: {invalid}"


def test_valid_evidence_types() -> None:
    valid = {m.value for m in EvidenceType}
    bad = []
    for s in SCENARIO_CATALOG:
        for ev in s.evidence_types:
            if ev.value not in valid:
                bad.append((s.id, ev))
    assert not bad, f"Invalid evidence_type values: {bad}"


def test_valid_capabilities() -> None:
    valid = {m.value for m in Capability}
    bad = []
    for s in SCENARIO_CATALOG:
        for cap in s.required_capabilities:
            if cap.value not in valid:
                bad.append((s.id, cap))
    assert not bad, f"Invalid capability values: {bad}"


def test_required_capabilities_non_empty() -> None:
    """Every spec needs at least one required capability (even CHAT for universal)."""
    empty = [s.id for s in SCENARIO_CATALOG if not s.required_capabilities]
    assert not empty, f"Specs with empty required_capabilities: {empty}"


def test_evidence_types_non_empty() -> None:
    """Every spec must list at least one evidence type."""
    empty = [s.id for s in SCENARIO_CATALOG if not s.evidence_types]
    assert not empty, f"Specs with empty evidence_types: {empty}"


def test_base_impact_in_range() -> None:
    out_of_range = [
        (s.id, s.base_impact)
        for s in SCENARIO_CATALOG
        if not (0.0 <= s.base_impact <= 10.0)
    ]
    assert not out_of_range, f"base_impact out of [0,10]: {out_of_range}"


def test_attack_scenario_accepts_catalog_fields() -> None:
    """AttackScenario model must accept all new catalog taxonomy fields."""
    from nuguard.redteam.scenarios.scenario_types import AttackScenario
    from nuguard.models.exploit_chain import GoalType, ScenarioType, ExploitChain, ExploitStep

    chain_id = "test-chain-id"
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.DATA_EXFILTRATION,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
    )
    spec = CATALOG_BY_ID["D01"]
    scenario = AttackScenario(
        scenario_id="test-scenario-id",
        goal_type=GoalType.DATA_EXFILTRATION,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        title="Test",
        description="Test scenario",
        catalog_id=spec.id,
        category=spec.category.value,
        delivery_channel=spec.delivery_channel,
        source_trust=spec.source_trust,
        sink_type=spec.sink_type,
        evidence_types=list(spec.evidence_types),
        safe_execution=spec.safe_execution,
        required_capabilities=[c.value for c in spec.required_capabilities],
        expected_control=spec.expected_control,
        chain=chain,
    )
    assert scenario.catalog_id == "D01"
    assert scenario.delivery_channel is not None
    assert len(scenario.evidence_types) > 0


def test_enabled_specs_have_non_stub_factory() -> None:
    """All enabled specs must point to a real (non-stub) factory function."""
    for spec in SCENARIO_CATALOG:
        if not spec.enabled:
            continue
        key = spec.resolved_builder_key()
        factory = BUILDER_FACTORIES.get(key)
        assert factory is not None, f"{spec.id}: no factory for key {key!r}"
        # Stubs are created by _stub() and have '__name__' starting with '_stub_'
        assert not factory.__name__.startswith("_stub_"), (
            f"{spec.id} is enabled but its factory {key!r} is a stub. "
            "Either disable the spec or implement the factory."
        )


@pytest.mark.parametrize("spec_id", [s.id for s in SCENARIO_CATALOG])
def test_spec_fields_snapshot(spec_id: str) -> None:
    """Snapshot each spec's key taxonomy fields to catch silent regressions."""
    spec = CATALOG_BY_ID[spec_id]
    # All frozen-dataclass fields that are enum instances
    assert isinstance(spec.category, ScenarioCategory)
    assert isinstance(spec.delivery_channel, DeliveryChannel)
    assert isinstance(spec.source_trust, SourceTrust)
    assert isinstance(spec.sink_type, SinkType)
    assert isinstance(spec.safe_execution, SafeExecution)
    assert spec.title, f"{spec_id}: title must be non-empty"
    assert spec.expected_control, f"{spec_id}: expected_control must be non-empty"
    assert spec.success_signal, f"{spec_id}: success_signal must be non-empty"


def test_owasp_llm_coverage() -> None:
    """Key OWASP LLM 2025 categories must appear in at least one spec."""
    required = {"LLM01", "LLM02", "LLM04", "LLM05", "LLM06", "LLM08", "LLM09", "LLM10"}
    covered: set[str] = set()
    for spec in SCENARIO_CATALOG:
        covered.update(spec.owasp_llm)
    missing = required - covered
    assert not missing, f"OWASP LLM categories not covered by any spec: {missing}"


def test_owasp_agentic_coverage() -> None:
    """Key OWASP Agentic 2026 categories must appear in at least one spec."""
    required = {"ASI01", "ASI02", "ASI03", "ASI04", "ASI05", "ASI06", "ASI07", "ASI09"}
    covered: set[str] = set()
    for spec in SCENARIO_CATALOG:
        covered.update(spec.owasp_agentic)
    missing = required - covered
    assert not missing, f"OWASP Agentic categories not covered by any spec: {missing}"
