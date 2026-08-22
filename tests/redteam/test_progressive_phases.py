"""Tests for the progressive-mode named phase taxonomy (docs/claude-redteam-3.md)."""
from __future__ import annotations

from nuguard.models.exploit_chain import ScenarioType
from nuguard.redteam.scenarios.phases import PROGRESSIVE_PHASES, progressive_phase_for


def test_progressive_phases_cover_ids_0_to_12():
    ids = [p.id for p in PROGRESSIVE_PHASES]
    assert ids == list(range(13))


def test_progressive_phases_have_names_and_purpose():
    for phase in PROGRESSIVE_PHASES:
        assert phase.name
        assert phase.purpose


def test_progressive_phase_for_new_scenario_types():
    assert progressive_phase_for(ScenarioType.OFF_TOPIC_PROBE.value) == 2
    assert progressive_phase_for(ScenarioType.SCRIPTED_IDENTITY_ESCALATION.value) == 3
    assert progressive_phase_for(ScenarioType.SCRIPTED_ROLE_ESCALATION.value) == 4
    assert progressive_phase_for(ScenarioType.DOCUMENT_MEMORY_POISONING.value) == 8
    assert progressive_phase_for(ScenarioType.RECOVERY_VERIFICATION.value) == 12


def test_progressive_phase_for_reused_scenario_types():
    assert progressive_phase_for(ScenarioType.REFUSAL_ORACLE.value) == 1
    assert progressive_phase_for(ScenarioType.CROSS_TENANT_EXFILTRATION.value) == 7
    assert progressive_phase_for(ScenarioType.HITL_BYPASS.value) == 9
    assert progressive_phase_for(ScenarioType.MULTI_AGENT_TRUST.value) == 11


def test_progressive_phase_for_unmapped_scenario_type_falls_back():
    # Any ScenarioType not explicitly reassigned still gets a phase from the
    # legacy 1-9 table via the fallback scale, never crashes or returns None.
    for st in ScenarioType:
        phase_id = progressive_phase_for(st.value)
        assert 0 <= phase_id <= 12
