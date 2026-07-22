"""Tests for RemediationSynthesizer's finding classification.

Covers the scenario_type-first classification fix: redteam findings now
carry AttackScenario.scenario_type, which identifies the specific attack
technique rather than the coarse goal_type family, so remediation routing
no longer collapses e.g. "Approval State Forgery" and "System Prompt
Extraction" (both PROMPT_DRIVEN_THREAT) into the same template.
"""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import ScenarioType
from nuguard.remediation.models import RemediationArtefactType
from nuguard.remediation.synthesizer import (
    _GOAL_TYPE_DTYPE,
    _SCENARIO_TYPE_DTYPE,
    RemediationSynthesizer,
    _classify_finding,
    _merge_artefacts,
)


class TestScenarioTypeExhaustiveness:
    def test_every_scenario_type_has_a_bucket(self) -> None:
        missing = [s.value for s in ScenarioType if s.value not in _SCENARIO_TYPE_DTYPE]
        assert not missing, f"ScenarioType values with no remediation bucket: {missing}"

    def test_no_unknown_scenario_type_keys(self) -> None:
        # Catches typos/stale entries: every dict key must be a real enum value.
        valid = {s.value for s in ScenarioType}
        stale = [k for k in _SCENARIO_TYPE_DTYPE if k not in valid]
        assert not stale, f"_SCENARIO_TYPE_DTYPE keys with no matching ScenarioType: {stale}"


class TestScenarioTypeTakesPriorityOverGoalType:
    def test_approval_state_forgery_routes_to_hitl_not_blocked_topics(self) -> None:
        # The motivating bug: this finding's goal_type is PROMPT_DRIVEN_THREAT
        # (which maps to blocked_topics_missing), but its scenario_type is the
        # much more specific APPROVAL_STATE_FORGERY, which must win.
        finding = {
            "finding_id": "f1",
            "title": "Approval State Forgery — ManagerBot",
            "description": "Attacker injected a fake HUMAN APPROVAL RECEIVED marker.",
            "goal_type": "prompt_driven_threat",
            "scenario_type": "APPROVAL_STATE_FORGERY",
        }
        assert _classify_finding(finding) == "hitl_gate_missing"

    def test_unmapped_scenario_type_falls_back_to_goal_type(self) -> None:
        finding = {
            "finding_id": "f1",
            "title": "Some future technique",
            "description": "n/a",
            "goal_type": "data_exfiltration",
            "scenario_type": "SOME_FUTURE_TYPE_NOT_YET_MAPPED",
        }
        assert _classify_finding(finding) == "data_leak"


class TestBackwardCompatNoScenarioType:
    """Findings without scenario_type (pre-change data, or behavior/analysis
    findings which never populate it) must classify identically to before
    this change — goal_type -> heuristic, unaffected by scenario_type."""

    @pytest.mark.parametrize("goal_type,expected_dtype", list(_GOAL_TYPE_DTYPE.items()))
    def test_goal_type_only_matches_existing_mapping(self, goal_type, expected_dtype) -> None:
        finding = {
            "finding_id": "f1",
            "title": "x",
            "description": "x",
            "goal_type": goal_type,
        }
        assert _classify_finding(finding) == expected_dtype

    def test_missing_scenario_type_key_entirely_does_not_error(self) -> None:
        finding = {"finding_id": "f1", "title": "x", "description": "x", "goal_type": "risky_tool_typo"}
        # Unknown goal_type and no scenario_type -> falls through to heuristics.
        assert _classify_finding(finding) == "generic"


class TestNewBucketHandlers:
    @pytest.mark.asyncio
    async def test_agentic_trust_boundary_produces_architectural_change(self) -> None:
        finding = {
            "finding_id": "f1",
            "title": "Cross-Agent Injection",
            "description": "Sub-agent output was treated as a trusted instruction.",
            "affected_component": "PlannerAgent",
            "severity": "high",
            "scenario_type": "CROSS_AGENT_INJECTION",
        }
        synth = RemediationSynthesizer()
        artefacts = await synth.synthesize_findings_async([finding])
        assert artefacts
        assert artefacts[0].artefact_type == RemediationArtefactType.ARCHITECTURAL_CHANGE

    @pytest.mark.asyncio
    async def test_memory_session_integrity_produces_architectural_change(self) -> None:
        finding = {
            "finding_id": "f1",
            "title": "Memory Poisoning",
            "description": "Attacker-controlled content persisted into agent memory.",
            "affected_component": "SupportAgent",
            "severity": "high",
            "scenario_type": "MEMORY_POISONING",
        }
        synth = RemediationSynthesizer()
        artefacts = await synth.synthesize_findings_async([finding])
        assert artefacts
        assert artefacts[0].artefact_type == RemediationArtefactType.ARCHITECTURAL_CHANGE

    @pytest.mark.asyncio
    async def test_output_handling_produces_output_guardrail(self) -> None:
        finding = {
            "finding_id": "f1",
            "title": "Output XSS Injection",
            "description": "Model output rendered unsanitized in the client UI.",
            "affected_component": "SupportAgent",
            "severity": "high",
            "scenario_type": "OUTPUT_XSS_INJECTION",
        }
        synth = RemediationSynthesizer()
        artefacts = await synth.synthesize_findings_async([finding])
        assert artefacts
        assert artefacts[0].artefact_type == RemediationArtefactType.OUTPUT_GUARDRAIL

    @pytest.mark.asyncio
    async def test_supply_chain_secrets_produces_input_guardrail(self) -> None:
        finding = {
            "finding_id": "f1",
            "title": "Env Var Probe",
            "description": "Agent disclosed the value of an environment variable.",
            "affected_component": "CIAgent",
            "severity": "medium",
            "scenario_type": "ENV_VAR_PROBE",
        }
        synth = RemediationSynthesizer()
        artefacts = await synth.synthesize_findings_async([finding])
        assert artefacts
        assert artefacts[0].artefact_type == RemediationArtefactType.INPUT_GUARDRAIL


class TestMergeArtefactsPreservesRationale:
    def test_two_artefacts_same_component_and_type_keep_both_rationales(self) -> None:
        # Exercises _merge_artefacts() directly, isolated from the
        # pre-merge content-based dedup in synthesize_findings_async
        # (covered separately in TestContentBasedDedup below).
        from nuguard.remediation.models import RemediationArtefact

        a1 = RemediationArtefact(
            finding_ids=["f1"],
            component="SupportAgent",
            component_type="AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority="high",
            patch_text="text1",
            rationale="Agent disclosed its full system prompt verbatim.",
        )
        a2 = RemediationArtefact(
            finding_ids=["f2"],
            component="SupportAgent",
            component_type="AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority="high",
            patch_text="text2",
            rationale="Multi-turn escalation reached restricted territory.",
        )
        merged = _merge_artefacts([a1, a2])
        assert len(merged) == 1
        assert "Agent disclosed its full system prompt verbatim." in merged[0].rationale
        assert "Multi-turn escalation reached restricted territory." in merged[0].rationale
        assert "Merged 2 system prompt patches" not in merged[0].rationale


def test_merge_artefacts_dedupes_identical_rationale() -> None:
    from nuguard.remediation.models import RemediationArtefact

    a1 = RemediationArtefact(
        finding_ids=["f1"],
        component="Agent",
        component_type="AGENT",
        artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
        priority="high",
        patch_text="text1",
        rationale="same rationale",
    )
    a2 = RemediationArtefact(
        finding_ids=["f2"],
        component="Agent",
        component_type="AGENT",
        artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
        priority="high",
        patch_text="text2",
        rationale="same rationale",
    )
    merged = _merge_artefacts([a1, a2])
    assert len(merged) == 1
    assert merged[0].rationale == "same rationale"


class TestContentBasedDedup:
    """The pre-merge dedup in synthesize_findings_async/synthesize_findings
    used to key on component:artefact_type:patch_section — for
    _patch_blocked_topics and _patch_generic_violation, patch_section is a
    pure function of component alone, so a second distinct finding on the
    same component produced an identical key and was silently dropped
    before ever reaching _merge_artefacts. _artefact_dedup_key() fixes this
    by keying on the artefact's actual instructive content + rationale
    instead of its display label."""

    @pytest.mark.asyncio
    async def test_distinct_findings_same_component_both_survive_async(self) -> None:
        # Two different blocked_topics_missing findings on the same agent:
        # same patch_section ("Out of Scope — SupportAgent") for both, but
        # genuinely different rationale. Previously the second was dropped
        # by the label-keyed dedup before _merge_artefacts ever ran.
        findings = [
            {
                "finding_id": "f1",
                "title": "System Prompt Extraction",
                "description": "Agent disclosed its full system prompt verbatim.",
                "affected_component": "SupportAgent",
                "severity": "high",
                "scenario_type": "SYSTEM_PROMPT_EXTRACTION",
            },
            {
                "finding_id": "f2",
                "title": "Crescendo",
                "description": "Multi-turn escalation reached restricted territory.",
                "affected_component": "SupportAgent",
                "severity": "high",
                "scenario_type": "CRESCENDO",
            },
        ]
        synth = RemediationSynthesizer()
        artefacts = await synth.synthesize_findings_async(findings)
        patches = [
            a for a in artefacts
            if a.component == "SupportAgent" and a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH
        ]
        assert len(patches) == 1, "expected both findings' artefacts to survive and merge into one"
        assert "Agent disclosed its full system prompt verbatim." in patches[0].rationale
        assert "Multi-turn escalation reached restricted territory." in patches[0].rationale

    def test_distinct_findings_same_component_both_survive_sync(self) -> None:
        # Same scenario via the sync entry point (synthesize_findings), which
        # had the identical bug duplicated in its own copy of the dedup loop.
        findings = [
            {
                "finding_id": "f1",
                "title": "System Prompt Extraction",
                "description": "Agent disclosed its full system prompt verbatim.",
                "affected_component": "SupportAgent",
                "severity": "high",
                "scenario_type": "SYSTEM_PROMPT_EXTRACTION",
            },
            {
                "finding_id": "f2",
                "title": "Crescendo",
                "description": "Multi-turn escalation reached restricted territory.",
                "affected_component": "SupportAgent",
                "severity": "high",
                "scenario_type": "CRESCENDO",
            },
        ]
        synth = RemediationSynthesizer()
        artefacts = synth.synthesize_findings(findings)
        patches = [
            a for a in artefacts
            if a.component == "SupportAgent" and a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH
        ]
        assert len(patches) == 1
        assert "Agent disclosed its full system prompt verbatim." in patches[0].rationale
        assert "Multi-turn escalation reached restricted territory." in patches[0].rationale

    @pytest.mark.asyncio
    async def test_true_duplicate_content_still_collapses_to_one(self) -> None:
        # Two findings that happen to produce byte-identical artefact
        # content AND rationale (e.g. re-processing, or two findings whose
        # descriptions happen to coincide) should still collapse to one
        # artefact rather than doubling up redundant text.
        findings = [
            {
                "finding_id": "f1",
                "title": "Env Var Probe",
                "description": "Agent disclosed an environment variable value.",
                "affected_component": "CIAgent",
                "severity": "medium",
                "scenario_type": "ENV_VAR_PROBE",
            },
            {
                "finding_id": "f2",
                "title": "Env Var Probe",
                "description": "Agent disclosed an environment variable value.",
                "affected_component": "CIAgent",
                "severity": "medium",
                "scenario_type": "ENV_VAR_PROBE",
            },
        ]
        synth = RemediationSynthesizer()
        artefacts = await synth.synthesize_findings_async(findings)
        guardrails = [a for a in artefacts if a.component == "CIAgent"]
        assert len(guardrails) == 1
        assert guardrails[0].finding_ids == ["f1"]


class TestCrossComponentIsolation:
    """component is the first field in both the dedup key
    (_artefact_dedup_key) and the merge grouping key (_merge_artefacts) —
    these tests make that isolation explicit rather than leaving it implicit
    in the key/grouping design, since a regression here would silently leak
    one agent's remediation rationale into an unrelated agent's artefact."""

    def test_merge_artefacts_keeps_different_components_separate(self) -> None:
        from nuguard.remediation.models import RemediationArtefact

        a1 = RemediationArtefact(
            finding_ids=["f1"],
            component="AgentA",
            component_type="AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority="high",
            patch_text="patch for A",
            rationale="AgentA-specific rationale",
        )
        a2 = RemediationArtefact(
            finding_ids=["f2"],
            component="AgentB",
            component_type="AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority="high",
            patch_text="patch for B",
            rationale="AgentB-specific rationale",
        )
        merged = _merge_artefacts([a1, a2])

        assert len(merged) == 2
        by_component = {a.component: a for a in merged}
        assert by_component["AgentA"].rationale == "AgentA-specific rationale"
        assert by_component["AgentB"].rationale == "AgentB-specific rationale"
        # No cross-contamination: each component's artefact must not carry
        # the other component's rationale or patch text.
        assert "AgentB" not in by_component["AgentA"].rationale
        assert "patch for B" not in by_component["AgentA"].patch_text
        assert "AgentA" not in by_component["AgentB"].rationale
        assert "patch for A" not in by_component["AgentB"].patch_text

    @pytest.mark.asyncio
    async def test_full_pipeline_keeps_different_components_separate(self) -> None:
        # Same scenario_type, same dtype (so they'd share a component-derived
        # display label if component weren't part of the dedup/merge keys),
        # but two different agents — must produce two independent artefacts.
        findings = [
            {
                "finding_id": "f1",
                "title": "System Prompt Extraction — AgentA",
                "description": "AgentA disclosed its system prompt to the attacker.",
                "affected_component": "AgentA",
                "severity": "high",
                "scenario_type": "SYSTEM_PROMPT_EXTRACTION",
            },
            {
                "finding_id": "f2",
                "title": "System Prompt Extraction — AgentB",
                "description": "AgentB disclosed its system prompt to the attacker.",
                "affected_component": "AgentB",
                "severity": "high",
                "scenario_type": "SYSTEM_PROMPT_EXTRACTION",
            },
        ]
        synth = RemediationSynthesizer()
        artefacts = await synth.synthesize_findings_async(findings)
        patches = [a for a in artefacts if a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH]

        assert len(patches) == 2, "expected one independent artefact per component, not merged"
        by_component = {a.component: a for a in patches}
        assert set(by_component) == {"AgentA", "AgentB"}
        assert "AgentA disclosed" in by_component["AgentA"].rationale
        assert "AgentB disclosed" not in by_component["AgentA"].rationale
        assert "AgentB disclosed" in by_component["AgentB"].rationale
        assert "AgentA disclosed" not in by_component["AgentB"].rationale
