"""Integrity and gating tests for the campaign scheduling sidecar."""
from __future__ import annotations

import re

from nuguard.redteam.catalog.registry import SCENARIO_CATALOG
from nuguard.redteam.catalog.scheduling import (
    LEVELS,
    AvailableFixtures,
    Fixture,
    SessionPolicy,
    blocked_fixture_reason,
    build_scheduling_table,
    scheduling_for,
)
from nuguard.redteam.catalog.taxonomy import SafeExecution

_ENABLED = [s for s in SCENARIO_CATALOG if s.enabled]
_TABLE = build_scheduling_table(SCENARIO_CATALOG)


def test_every_enabled_catalog_id_has_valid_scheduling_entry() -> None:
    assert set(_TABLE) == {s.id for s in _ENABLED}
    for cid, meta in _TABLE.items():
        assert meta.levels and all(lvl in LEVELS and lvl >= 1 for lvl in meta.levels), cid
        assert list(meta.levels) == sorted(set(meta.levels)), cid
        assert meta.technique_class and meta.control_id, cid


def test_prerequisites_reference_baseline_levels_or_enabled_ids_without_cycles() -> None:
    enabled = set(_TABLE)
    for cid, meta in _TABLE.items():
        for pre in meta.prerequisites:
            assert re.fullmatch(r"L[0-7]", pre) or pre in enabled, (cid, pre)
            assert pre != cid


def test_disabled_specs_are_not_scheduled() -> None:
    for s in SCENARIO_CATALOG:
        if not s.enabled:
            assert s.id not in _TABLE


def test_spec_table_levels_applied_including_multi_level_ids() -> None:
    assert _TABLE["E05"].levels == (1,)
    assert _TABLE["J03"].levels == (2,)
    assert _TABLE["D01"].levels == (4,)
    assert _TABLE["T01"].levels == (5,)
    assert _TABLE["T07"].levels == (5, 7)
    assert _TABLE["G05"].levels == (6, 7)
    assert _TABLE["T07"].primary_level == 5


def test_unlisted_entries_derive_level_from_category() -> None:
    assert _TABLE["A02"].levels == (4,)   # Authorization Failures, not in spec §3
    assert _TABLE["B05"].levels == (3,)   # Business Logic


def test_instruction_override_entries_taint_the_branch() -> None:
    for cid in ("J03", "J04", "J06", "E04"):
        assert "instruction_override" in _TABLE[cid].contamination_tags
    assert not _TABLE["D01"].contamination_tags


def test_persistent_and_destructive_families_get_isolated_sessions() -> None:
    assert _TABLE["T01"].session_policy == SessionPolicy.ISOLATED
    assert _TABLE["P01"].session_policy == SessionPolicy.ISOLATED
    assert _TABLE["D01"].session_policy == SessionPolicy.REUSE


def test_fixture_gating_follows_safe_execution() -> None:
    expected = {
        SafeExecution.SYNTHETIC_TENANT: Fixture.SECOND_PRINCIPAL,
        SafeExecution.TRAP_ENDPOINT: Fixture.CALLBACK_SERVER,
        SafeExecution.DRY_RUN_TOOL: Fixture.DECLARED_FIXTURE,
        SafeExecution.SANDBOX: Fixture.DECLARED_FIXTURE,
        SafeExecution.TRACE_ONLY: Fixture.NONE,
        SafeExecution.CANARY_ONLY: Fixture.NONE,
    }
    for s in _ENABLED:
        assert scheduling_for(s).requires_fixture == expected.get(
            s.safe_execution, Fixture.DECLARED_FIXTURE
        ), s.id


def test_blocked_fixture_reason() -> None:
    synthetic = next(s for s in _ENABLED if s.safe_execution == SafeExecution.SYNTHETIC_TENANT)
    meta = scheduling_for(synthetic)
    assert blocked_fixture_reason(meta, AvailableFixtures()) == "blocked_fixture:second_principal"
    assert blocked_fixture_reason(meta, AvailableFixtures(second_principal=True)) is None
    trace = scheduling_for(next(s for s in _ENABLED if s.safe_execution == SafeExecution.TRACE_ONLY))
    assert blocked_fixture_reason(trace, AvailableFixtures()) is None
