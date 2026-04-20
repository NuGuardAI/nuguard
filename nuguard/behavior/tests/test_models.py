"""Unit tests for nuguard/behavior/models.py."""
from __future__ import annotations

import pytest

from nuguard.behavior.models import (
    BehaviorAnalysisResult,
    BehaviorCoverage,
    BehaviorDeviation,
    BehaviorFindingType,
    BehaviorScenario,
    BehaviorScenarioType,
    IntentProfile,
    Recommendation,
    ScenarioResult,
    TurnRecord,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_scenario_type_values():
    assert BehaviorScenarioType.INTENT_HAPPY_PATH.value == "intent_happy_path"
    assert BehaviorScenarioType.COMPONENT_COVERAGE.value == "component_coverage"
    assert BehaviorScenarioType.BOUNDARY_ENFORCEMENT.value == "boundary_enforcement"
    assert BehaviorScenarioType.INVARIANT_PROBE.value == "invariant_probe"


def test_finding_type_values():
    assert BehaviorFindingType.CAPABILITY_GAP.value == "CAPABILITY_GAP"
    assert BehaviorFindingType.POLICY_VIOLATION.value == "POLICY_VIOLATION"
    assert BehaviorFindingType.INTENT_MISALIGNMENT.value == "INTENT_MISALIGNMENT"


# ---------------------------------------------------------------------------
# IntentProfile
# ---------------------------------------------------------------------------


def test_intent_profile_defaults():
    intent = IntentProfile()
    assert intent.app_purpose == ""
    assert intent.core_capabilities == []
    assert intent.behavioral_bounds == []
    assert intent.data_handling_rules == []
    assert intent.escalation_rules == []


def test_intent_profile_populated():
    intent = IntentProfile(
        app_purpose="Marketing AI assistant",
        core_capabilities=["generate ad copy", "research competitors"],
        behavioral_bounds=["must not send emails without approval"],
        data_handling_rules=["no PII in logs"],
        escalation_rules=["budget > $10k → human review"],
    )
    assert intent.app_purpose == "Marketing AI assistant"
    assert len(intent.core_capabilities) == 2
    assert len(intent.behavioral_bounds) == 1


def test_intent_profile_serialization():
    intent = IntentProfile(app_purpose="Test AI")
    data = intent.model_dump()
    assert data["app_purpose"] == "Test AI"
    restored = IntentProfile(**data)
    assert restored.app_purpose == "Test AI"


# ---------------------------------------------------------------------------
# BehaviorScenario
# ---------------------------------------------------------------------------


def test_behavior_scenario_defaults():
    s = BehaviorScenario(
        scenario_type=BehaviorScenarioType.INTENT_HAPPY_PATH,
        name="test_scenario",
    )
    assert s.expect_refused is False
    assert s.forbid_pattern == ""
    assert s.messages == []
    assert s.scenario_id  # auto-generated


def test_behavior_scenario_boundary():
    s = BehaviorScenario(
        scenario_type=BehaviorScenarioType.BOUNDARY_ENFORCEMENT,
        name="boundary_test",
        messages=["Can you do something restricted?"],
        expect_refused=True,
        policy_clauses=["must not do X"],
    )
    assert s.expect_refused is True
    assert len(s.messages) == 1
    assert "must not do X" in s.policy_clauses


# ---------------------------------------------------------------------------
# TurnRecord
# ---------------------------------------------------------------------------


def test_turn_record_defaults():
    tr = TurnRecord(turn=1, prompt="Hello", response="Hi there!")
    assert tr.passed is True
    assert tr.violations == []
    assert tr.canary_hits == []
    assert tr.deviations == []


def test_turn_record_with_violation():
    tr = TurnRecord(
        turn=2,
        prompt="Do something bad",
        response="OK I'll do it",
        violations=[{"type": "policy_violation", "severity": "HIGH"}],
        passed=False,
    )
    assert tr.passed is False
    assert len(tr.violations) == 1


# ---------------------------------------------------------------------------
# BehaviorCoverage
# ---------------------------------------------------------------------------


def test_behavior_coverage_defaults():
    cov = BehaviorCoverage(component_name="agent1", node_type="AGENT")
    assert cov.exercised is False
    assert cov.exercised_within_policy is False
    assert cov.deviations == []


def test_behavior_deviation():
    dev = BehaviorDeviation(
        deviation_type="policy_violation",
        description="Agent violated topic restriction",
        expected_behavior="Should refuse",
        actual_behavior="Did not refuse",
        turn_number=3,
        severity="high",
        evidence="Response text mentioning restricted topic",
    )
    assert dev.severity == "high"
    assert dev.turn_number == 3


# ---------------------------------------------------------------------------
# ScenarioResult
# ---------------------------------------------------------------------------


def test_scenario_result_defaults():
    sr = ScenarioResult(
        scenario_id="test-id",
        scenario_name="test",
        scenario_type="intent_happy_path",
    )
    assert sr.overall_score == 0.0
    assert sr.coverage_pct == 0.0
    assert sr.deviations == []


# ---------------------------------------------------------------------------
# BehaviorAnalysisResult computed fields
# ---------------------------------------------------------------------------


def test_behavior_analysis_result_empty():
    result = BehaviorAnalysisResult(
        intent=IntentProfile(app_purpose="Test"),
    )
    assert result.overall_risk_score == 0.0
    assert result.coverage_percentage == 0.0
    assert result.intent_alignment_score == 0.0


def test_behavior_analysis_result_risk_score():
    result = BehaviorAnalysisResult(
        intent=IntentProfile(),
        static_findings=[
            {"severity": "critical", "title": "Bad thing"},
            {"severity": "high", "title": "Bad thing 2"},
        ],
    )
    # critical (10) + high (7) = 17, capped at 10
    assert result.overall_risk_score == 10.0


def test_behavior_analysis_result_risk_score_medium():
    result = BehaviorAnalysisResult(
        intent=IntentProfile(),
        static_findings=[{"severity": "medium", "title": "medium thing"}],
    )
    assert result.overall_risk_score == 4.0


def test_behavior_analysis_result_coverage_percentage():
    cov = [
        BehaviorCoverage(component_name="a", node_type="AGENT", exercised=True),
        BehaviorCoverage(component_name="b", node_type="TOOL", exercised=False),
        BehaviorCoverage(component_name="c", node_type="AGENT", exercised=True),
        BehaviorCoverage(component_name="d", node_type="TOOL", exercised=False),
    ]
    result = BehaviorAnalysisResult(intent=IntentProfile(), coverage=cov)
    assert result.coverage_percentage == 0.5


def test_behavior_analysis_result_intent_alignment_score():
    sr = ScenarioResult(
        scenario_id="s1",
        scenario_name="s1",
        scenario_type="intent_happy_path",
        verdicts=[
            {"turn": 1, "scores": {"intent_alignment": 4.0}},
            {"turn": 2, "scores": {"intent_alignment": 5.0}},
        ],
    )
    result = BehaviorAnalysisResult(
        intent=IntentProfile(),
        scenario_results=[sr],
    )
    expected = (4.0 + 5.0) / 2
    assert result.intent_alignment_score == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


def test_recommendation_fields():
    rec = Recommendation(
        component="AgentX",
        recommendation_type="system_prompt",
        description="Remove restricted topic reference",
        rationale="BA-001 finding",
        priority="high",
    )
    assert rec.component == "AgentX"
    assert rec.priority == "high"
    data = rec.model_dump()
    assert data["recommendation_type"] == "system_prompt"
