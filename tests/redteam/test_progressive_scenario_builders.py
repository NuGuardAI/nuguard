"""Tests for the new progressive-methodology scenario builders (docs/claude-redteam-3.md §5)."""
from __future__ import annotations

from nuguard.models.exploit_chain import ScenarioType
from nuguard.redteam.scenarios.agent_identity import (
    build_scripted_identity_confusion,
    build_scripted_role_confusion,
)
from nuguard.redteam.scenarios.data_exfiltration import (
    build_cross_tenant_exfiltration,
    build_cross_tenant_fictional_framing,
)
from nuguard.redteam.scenarios.memory_persistence import build_document_memory_poisoning
from nuguard.redteam.scenarios.off_topic import build_off_topic_probes
from nuguard.redteam.scenarios.recovery import build_recovery_verification


def test_scripted_identity_confusion_is_5_turns_and_never_aborts():
    scenario = build_scripted_identity_confusion("agent-1", "Support Agent")
    assert scenario.scenario_type == ScenarioType.SCRIPTED_IDENTITY_ESCALATION
    assert scenario.chain is not None
    assert len(scenario.chain.steps) == 5
    assert all(step.on_failure == "skip" for step in scenario.chain.steps)
    assert "account owner" in scenario.chain.steps[0].payload.lower()


def test_scripted_role_confusion_is_6_turns_and_never_aborts():
    scenario = build_scripted_role_confusion("agent-1", "Support Agent")
    assert scenario.scenario_type == ScenarioType.SCRIPTED_ROLE_ESCALATION
    assert scenario.chain is not None
    assert len(scenario.chain.steps) == 6
    assert all(step.on_failure == "skip" for step in scenario.chain.steps)
    assert "administrator" in scenario.chain.steps[2].payload.lower()


def test_off_topic_probes_include_universal_examples():
    scenarios = build_off_topic_probes("agent-1", "Support Agent")
    assert len(scenarios) >= 3
    assert all(s.scenario_type == ScenarioType.OFF_TOPIC_PROBE for s in scenarios)
    payloads = [s.chain.steps[0].payload for s in scenarios]
    assert any("sports scores" in p.lower() for p in payloads)


def test_off_topic_probes_add_restricted_topics():
    scenarios = build_off_topic_probes("agent-1", "Support Agent", restricted_topics=["tax advice"])
    payloads = [s.chain.steps[0].payload for s in scenarios]
    assert any("tax advice" in p for p in payloads)


def test_document_memory_poisoning_two_turns():
    scenario = build_document_memory_poisoning("agent-1", "Support Agent", restricted_action="skip approval")
    assert scenario.scenario_type == ScenarioType.DOCUMENT_MEMORY_POISONING
    assert len(scenario.chain.steps) == 2
    assert "skip approval" in scenario.chain.steps[0].payload
    assert scenario.chain.steps[1].contributes_to_finding is True


def test_recovery_verification_two_turns():
    scenario = build_recovery_verification("agent-1", "Support Agent", baseline_prompt="What can you do?")
    assert scenario.scenario_type == ScenarioType.RECOVERY_VERIFICATION
    assert len(scenario.chain.steps) == 2
    assert scenario.chain.steps[1].payload == "What can you do?"


def test_cross_tenant_exfiltration_uses_real_tenant_id_when_provided():
    scenario = build_cross_tenant_exfiltration("agent-1", "Support Agent", real_tenant_id="tenant-real-42")
    payloads = " ".join(step.payload for step in scenario.chain.steps)
    assert "tenant-real-42" in payloads


def test_cross_tenant_exfiltration_falls_back_to_random_probe():
    scenario = build_cross_tenant_exfiltration("agent-1", "Support Agent")
    payloads = " ".join(step.payload for step in scenario.chain.steps)
    assert "tenant-probe-" in payloads


def test_cross_tenant_fictional_framing_shape():
    scenario = build_cross_tenant_fictional_framing("agent-1", "Support Agent")
    assert scenario.scenario_type == ScenarioType.CROSS_TENANT_EXFILTRATION
    assert len(scenario.chain.steps) == 2
    assert "fiction" in scenario.chain.steps[1].payload.lower()
