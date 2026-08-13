"""Unit tests for nuguard.redteam.risk_engine module.

score_finding() now returns an NGRSResult (score + severity + vector) driven
by the evidence supplied, not a bare Severity from a flat goal-type lookup —
see nuguard.redteam.risk_engine.ngrs for the model. Exact score/band values
are a first-pass calibration explicitly meant to be tuned against
tests/redteam/test_judge_corpus.py's labeled corpus, so most assertions here
check monotonicity/ordering properties (stronger evidence never scores lower)
rather than hardcoded exact bands, which would need updating on every
calibration pass.
"""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import GoalType
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.risk_engine import (
    aggregate_score,
    highest_severity,
    owasp_asi_ref,
    owasp_llm_ref,
    rescore_with_probe,
    score_finding,
    score_policy_violation,
)


def _finding(severity: Severity, finding_id: str = "f1", ngrs_score: int | None = None) -> Finding:
    return Finding(
        finding_id=finding_id,
        title="test",
        severity=severity,
        description="test",
        ngrs_score=ngrs_score,
    )


# --- score_finding -------------------------------------------------------------


def test_score_finding_returns_ngrs_result_with_score_and_vector() -> None:
    result = score_finding(GoalType.DATA_EXFILTRATION)
    assert 0 <= result.score <= 100
    assert isinstance(result.severity, Severity)
    assert result.vector  # non-empty, parseable factor string


def test_score_data_exfiltration_canary_beats_no_evidence() -> None:
    canary = score_finding(GoalType.DATA_EXFILTRATION, canary_verbatim=True)
    baseline = score_finding(GoalType.DATA_EXFILTRATION)
    assert canary.score > baseline.score


def test_score_data_exfiltration_phi_beats_plain_canary() -> None:
    plain = score_finding(GoalType.DATA_EXFILTRATION, canary_verbatim=True)
    phi = score_finding(GoalType.DATA_EXFILTRATION, canary_verbatim=True, phi_exposed=True)
    assert phi.score >= plain.score


def test_score_data_exfiltration_cross_tenant_beats_same_tenant() -> None:
    same_tenant = score_finding(GoalType.DATA_EXFILTRATION, canary_verbatim=True)
    cross_tenant = score_finding(GoalType.DATA_EXFILTRATION, canary_verbatim=True, cross_tenant=True)
    assert cross_tenant.score >= same_tenant.score


def test_score_privilege_escalation_high_privilege_chain_beats_baseline() -> None:
    escalated = score_finding(GoalType.PRIVILEGE_ESCALATION, high_privilege_chain=True, deterministic_evidence=True)
    baseline = score_finding(GoalType.PRIVILEGE_ESCALATION)
    assert escalated.score > baseline.score


def test_score_mcp_toxic_flow_scores_high_or_critical_when_confirmed() -> None:
    result = score_finding(GoalType.MCP_TOXIC_FLOW, deterministic_evidence=True)
    assert result.severity in (Severity.HIGH, Severity.CRITICAL)


def test_score_evidence_strength_ordering() -> None:
    """Deterministic > LLM-high > LLM-medium > keyword-only, for the same goal type."""
    deterministic = score_finding(GoalType.PROMPT_DRIVEN_THREAT, deterministic_evidence=True)
    llm_high = score_finding(GoalType.PROMPT_DRIVEN_THREAT, llm_confidence="high")
    llm_medium = score_finding(GoalType.PROMPT_DRIVEN_THREAT, llm_confidence="medium")
    keyword_only = score_finding(GoalType.PROMPT_DRIVEN_THREAT)
    assert deterministic.score >= llm_high.score >= llm_medium.score >= keyword_only.score


def test_score_probe_modifier_confirmed_beats_unconfirmed() -> None:
    confirmed = score_finding(GoalType.DATA_EXFILTRATION, llm_confidence="high", probe_modifier=1)
    unconfirmed = score_finding(GoalType.DATA_EXFILTRATION, llm_confidence="high", probe_modifier=-1)
    assert confirmed.score >= unconfirmed.score


def test_score_finding_unknown_goal_type_falls_back_to_default_impact() -> None:
    # Any GoalType not in _GOAL_TYPE_DEFAULT_IMPACT should still score without error.
    result = score_finding(GoalType.RECON_INFERENCE)
    assert 0 <= result.score <= 100


# --- score_policy_violation -----------------------------------------------------


def test_score_policy_violation_topic_boundary_tier1_beats_tier2() -> None:
    """Tier-1 (restricted-topic hit) is more severe than Tier-2 (no allowed-topic overlap)."""
    tier1 = score_policy_violation("topic_boundary", "restricted_topics: x", 0.9, GoalType.POLICY_VIOLATION)
    tier2 = score_policy_violation(
        "topic_boundary", "allowed_topics (no overlap)", 0.4, GoalType.POLICY_VIOLATION
    )
    assert tier1.score > tier2.score


def test_score_policy_violation_higher_detector_confidence_scores_higher() -> None:
    low_conf = score_policy_violation("data_classification", "x", 0.3, GoalType.POLICY_VIOLATION)
    high_conf = score_policy_violation("data_classification", "x", 0.95, GoalType.POLICY_VIOLATION)
    assert high_conf.score >= low_conf.score


# --- rescore_with_probe ----------------------------------------------------------


def test_rescore_with_probe_reproduced_beats_unconfirmed() -> None:
    base = score_finding(GoalType.DATA_EXFILTRATION, canary_verbatim=True)
    reproduced = rescore_with_probe(base.vector, probe_modifier=1)
    unconfirmed = rescore_with_probe(base.vector, probe_modifier=-1)
    assert reproduced.score >= unconfirmed.score


def test_rescore_with_probe_round_trips_vector() -> None:
    base = score_finding(GoalType.PRIVILEGE_ESCALATION, high_privilege_chain=True)
    rescored = rescore_with_probe(base.vector, probe_modifier=0)
    assert rescored.score == base.score
    assert rescored.severity == base.severity


# --- owasp_llm_ref -----------------------------------------------------------


def test_owasp_llm_ref_prompt_driven_threat_starts_llm01() -> None:
    ref = owasp_llm_ref(GoalType.PROMPT_DRIVEN_THREAT)
    assert ref is not None
    assert ref.startswith("LLM01")


def test_owasp_llm_ref_data_exfiltration_starts_llm06() -> None:
    ref = owasp_llm_ref(GoalType.DATA_EXFILTRATION)
    assert ref is not None
    assert ref.startswith("LLM06")


def test_owasp_llm_ref_prompt_driven_threat_exact_value() -> None:
    ref = owasp_llm_ref(GoalType.PROMPT_DRIVEN_THREAT)
    assert ref == "LLM01 – Prompt Injection"


def test_owasp_llm_ref_returns_string_for_all_goal_types() -> None:
    for goal_type in GoalType:
        ref = owasp_llm_ref(goal_type)
        assert isinstance(ref, str) and ref


# --- owasp_asi_ref -----------------------------------------------------------


def test_owasp_asi_ref_prompt_driven_threat_starts_asi01() -> None:
    ref = owasp_asi_ref(GoalType.PROMPT_DRIVEN_THREAT)
    assert ref is not None
    assert ref.startswith("ASI01")


def test_owasp_asi_ref_returns_string_for_all_goal_types() -> None:
    for goal_type in GoalType:
        ref = owasp_asi_ref(goal_type)
        assert isinstance(ref, str) and ref


# --- aggregate_score ---------------------------------------------------------


def test_aggregate_score_empty_returns_zero() -> None:
    assert aggregate_score([]) == pytest.approx(0.0)


def test_aggregate_score_single_critical_positive() -> None:
    findings = [_finding(Severity.CRITICAL, ngrs_score=95)]
    score = aggregate_score(findings)
    assert score > 0.0


def test_aggregate_score_uses_ngrs_score_when_present() -> None:
    findings = [_finding(Severity.CRITICAL, ngrs_score=90)]
    assert aggregate_score(findings) == pytest.approx(9.0)


def test_aggregate_score_falls_back_to_severity_weight_without_ngrs_score() -> None:
    findings = [_finding(Severity.CRITICAL, ngrs_score=None)]
    assert aggregate_score(findings) == pytest.approx(9.0)


def test_aggregate_score_more_criticals_same_score_averages_equal() -> None:
    one_critical = aggregate_score([_finding(Severity.CRITICAL, "f1", ngrs_score=90)])
    two_criticals = aggregate_score(
        [
            _finding(Severity.CRITICAL, "f1", ngrs_score=90),
            _finding(Severity.CRITICAL, "f2", ngrs_score=90),
        ]
    )
    assert one_critical == pytest.approx(two_criticals)


def test_aggregate_score_in_range_zero_to_ten() -> None:
    findings = [
        _finding(Severity.CRITICAL, "f1", ngrs_score=90),
        _finding(Severity.HIGH, "f2", ngrs_score=65),
        _finding(Severity.MEDIUM, "f3", ngrs_score=45),
    ]
    score = aggregate_score(findings)
    assert 0.0 <= score <= 10.0


def test_aggregate_score_all_critical_higher_than_all_low() -> None:
    criticals = [_finding(Severity.CRITICAL, f"f{i}", ngrs_score=90) for i in range(3)]
    lows = [_finding(Severity.LOW, f"l{i}", ngrs_score=20) for i in range(3)]
    assert aggregate_score(criticals) > aggregate_score(lows)


# --- highest_severity --------------------------------------------------------


def test_highest_severity_empty_returns_none() -> None:
    assert highest_severity([]) is None


def test_highest_severity_single_critical() -> None:
    assert highest_severity([_finding(Severity.CRITICAL)]) == Severity.CRITICAL


def test_highest_severity_mix_returns_critical() -> None:
    findings = [_finding(Severity.HIGH, "f1"), _finding(Severity.CRITICAL, "f2")]
    assert highest_severity(findings) == Severity.CRITICAL


def test_highest_severity_only_low() -> None:
    findings = [_finding(Severity.LOW, "f1"), _finding(Severity.LOW, "f2")]
    assert highest_severity(findings) == Severity.LOW
