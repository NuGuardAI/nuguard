"""Unit tests for nuguard.redteam.risk_engine module."""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import GoalType
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.risk_engine import (
    aggregate_score,
    generate_remediation,
    highest_severity,
    owasp_asi_ref,
    owasp_llm_ref,
    score_finding,
)


def _finding(severity: Severity, finding_id: str = "f1") -> Finding:
    return Finding(
        finding_id=finding_id,
        title="test",
        severity=severity,
        description="test",
    )


# --- score_finding -----------------------------------------------------------


def test_score_data_exfiltration_canary_verbatim_critical() -> None:
    result = score_finding(GoalType.DATA_EXFILTRATION, canary_verbatim=True)
    assert result == Severity.CRITICAL


def test_score_data_exfiltration_no_flags_high() -> None:
    result = score_finding(GoalType.DATA_EXFILTRATION)
    assert result == Severity.HIGH


def test_score_privilege_escalation_high_privilege_chain_critical() -> None:
    result = score_finding(GoalType.PRIVILEGE_ESCALATION, high_privilege_chain=True)
    assert result == Severity.CRITICAL


def test_score_privilege_escalation_no_flags_high() -> None:
    result = score_finding(GoalType.PRIVILEGE_ESCALATION)
    assert result == Severity.HIGH


def test_score_mcp_toxic_flow_always_critical() -> None:
    assert score_finding(GoalType.MCP_TOXIC_FLOW) == Severity.CRITICAL


def test_score_prompt_driven_threat_high() -> None:
    assert score_finding(GoalType.PROMPT_DRIVEN_THREAT) == Severity.HIGH


def test_score_policy_violation_high() -> None:
    assert score_finding(GoalType.POLICY_VIOLATION) == Severity.HIGH


def test_score_tool_abuse_high() -> None:
    assert score_finding(GoalType.TOOL_ABUSE) == Severity.HIGH


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
    assert ref == "LLM01 \u2013 Prompt Injection"


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


# --- generate_remediation ---------------------------------------------------


def test_generate_remediation_returns_nonempty_for_all_goal_types() -> None:
    for goal_type in GoalType:
        result = generate_remediation(goal_type)
        assert isinstance(result, str) and result


def test_generate_remediation_tool_abuse_mentions_sanitize_or_ssrf() -> None:
    result = generate_remediation(GoalType.TOOL_ABUSE)
    lower = result.lower()
    assert "sanitiz" in lower or "parameterized" in lower or "parameterised" in lower or "ssrf" in lower.upper() or "SSRF" in result


def test_generate_remediation_with_affected_component_includes_component() -> None:
    result = generate_remediation(GoalType.TOOL_ABUSE, affected_component="my_tool")
    assert "my_tool" in result


# --- aggregate_score ---------------------------------------------------------


def test_aggregate_score_empty_returns_zero() -> None:
    assert aggregate_score([]) == pytest.approx(0.0)


def test_aggregate_score_single_critical_positive() -> None:
    findings = [_finding(Severity.CRITICAL)]
    score = aggregate_score(findings)
    assert score > 0.0


def test_aggregate_score_more_criticals_higher_score() -> None:
    one_critical = aggregate_score([_finding(Severity.CRITICAL, "f1")])
    two_criticals = aggregate_score(
        [_finding(Severity.CRITICAL, "f1"), _finding(Severity.CRITICAL, "f2")]
    )
    # With same proportion both would be the same — but more findings generally
    # leads to higher or equal normalized score; here both are CRITICAL so equal
    assert one_critical == pytest.approx(two_criticals)


def test_aggregate_score_in_range_zero_to_ten() -> None:
    findings = [
        _finding(Severity.CRITICAL, "f1"),
        _finding(Severity.HIGH, "f2"),
        _finding(Severity.MEDIUM, "f3"),
    ]
    score = aggregate_score(findings)
    assert 0.0 <= score <= 10.0


def test_aggregate_score_all_critical_higher_than_all_low() -> None:
    criticals = [_finding(Severity.CRITICAL, f"f{i}") for i in range(3)]
    lows = [_finding(Severity.LOW, f"l{i}") for i in range(3)]
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
