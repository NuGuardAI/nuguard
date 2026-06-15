"""Tests for behavior report generation and risk score calculation."""
from __future__ import annotations

import pytest

from nuguard.behavior.models import BehaviorAnalysisResult, BehaviorCoverage, IntentProfile, ScenarioResult
from nuguard.behavior.report import to_markdown
from nuguard.cli.report_meta import ReportMeta


def _make_result(**kwargs) -> BehaviorAnalysisResult:
    defaults = dict(intent=IntentProfile(app_purpose="test app"))
    defaults.update(kwargs)
    return BehaviorAnalysisResult(**defaults)


def _finding(severity: str) -> dict:
    return {"title": f"{severity} issue", "severity": severity, "description": "desc"}


# ---------------------------------------------------------------------------
# overall_risk_score
# ---------------------------------------------------------------------------


def test_risk_score_no_findings():
    result = _make_result()
    assert result.overall_risk_score == 0.0


def test_risk_score_single_critical():
    result = _make_result(dynamic_findings=[_finding("critical")])
    assert result.overall_risk_score == 100.0


def test_risk_score_single_high():
    result = _make_result(dynamic_findings=[_finding("high")])
    assert result.overall_risk_score == 80.0


def test_risk_score_single_medium():
    result = _make_result(dynamic_findings=[_finding("medium")])
    assert result.overall_risk_score == 50.0


def test_risk_score_single_low():
    result = _make_result(dynamic_findings=[_finding("low")])
    assert result.overall_risk_score == 25.0


def test_risk_score_mixed_critical_and_low():
    # avg(100, 25) = 62.5
    result = _make_result(dynamic_findings=[_finding("critical"), _finding("low")])
    assert result.overall_risk_score == 62.5


def test_risk_score_two_highs_below_10():
    # avg(80, 80) = 80.0
    result = _make_result(dynamic_findings=[_finding("high"), _finding("high")])
    assert result.overall_risk_score == 80.0


def test_risk_score_uses_static_and_dynamic():
    result = _make_result(
        static_findings=[_finding("medium")],
        dynamic_findings=[_finding("high")],
    )
    # avg(50, 80) = 65.0
    assert result.overall_risk_score == 65.0


def test_risk_score_info_counts_as_zero_weight():
    result = _make_result(dynamic_findings=[_finding("info")])
    assert result.overall_risk_score == 0.0


# ---------------------------------------------------------------------------
# to_markdown — meta header rendering
# ---------------------------------------------------------------------------


def test_to_markdown_no_meta_omits_header_fields():
    result = _make_result()
    md = to_markdown(result, meta=None)
    assert "**Generated:**" not in md
    assert "**LLM:**" not in md
    assert "**Target:**" not in md


def test_to_markdown_with_meta_renders_header():
    result = _make_result()
    meta = ReportMeta(
        llm_models=["gemini/gemini-2.0-flash"],
        target_url="http://localhost:8080",
        target_endpoint="/api/chat",
    )
    md = to_markdown(result, meta=meta)
    assert "**Generated:**" in md
    assert "**LLM:** gemini/gemini-2.0-flash" in md
    assert "**Target:** `http://localhost:8080/api/chat`" in md


def test_to_markdown_meta_no_llm_shows_not_used():
    result = _make_result()
    meta = ReportMeta(llm_models=[], target_url="http://localhost:8080")
    md = to_markdown(result, meta=meta)
    assert "**LLM:** not used" in md


def test_to_markdown_meta_header_appears_before_summary():
    result = _make_result()
    meta = ReportMeta(llm_models=["gemini/gemini-2.0-flash"], target_url="http://localhost")
    md = to_markdown(result, meta=meta)
    generated_pos = md.index("**Generated:**")
    summary_pos = md.index("## Summary")
    assert generated_pos < summary_pos


# ---------------------------------------------------------------------------
# to_markdown — new validation report sections
# ---------------------------------------------------------------------------


def _make_scenario_result(
    name: str = "test_scenario",
    score: float = 4.0,
    verdicts: list[dict] | None = None,
    matched_topic: str | None = None,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id="s1",
        scenario_name=name,
        scenario_type="intent_happy_path",
        verdicts=verdicts or [],
        overall_score=score,
        total_turns=len(verdicts) if verdicts else 0,
        matched_topic=matched_topic,
    )


def test_to_markdown_contains_validation_summary_bullets():
    sr = _make_scenario_result(score=4.0)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "**Total Scenarios**" in md
    assert "**Success Rate**" in md
    assert "**Total Turns**" in md


def test_to_markdown_scenario_details_section_present():
    sr = _make_scenario_result(score=4.0)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "## Scenario Details" in md
    assert "### Scenario 1:" in md


def test_to_markdown_scenario_details_shows_request_response():
    verdicts = [
        {
            "turn": 1,
            "prompt": "What is my balance?",
            "response": "Your balance is $500.",
            "verdict": "PASS",
            "scores": {},
        }
    ]
    sr = _make_scenario_result(score=4.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "What is my balance?" in md
    assert "Your balance is $500." in md


def test_to_markdown_coverage_evidence_section():
    verdicts = [
        {
            "turn": 1,
            "prompt": "Book a flight",
            "response": "Using booking_agent to book…",
            "verdict": "PASS",
            "agents_mentioned": ["booking_agent"],
            "tools_mentioned": [],
            "scores": {},
        }
    ]
    sr = _make_scenario_result(score=4.0, verdicts=verdicts, matched_topic="flight booking")
    cov = BehaviorCoverage(
        component_name="booking_agent",
        node_type="AGENT",
        exercised=True,
        exercised_within_policy=True,
    )
    result = _make_result(scenario_results=[sr], coverage=[cov])
    md = to_markdown(result)
    assert "## Coverage Evidence" in md
    assert "booking_agent" in md
    assert "flight booking" in md


def test_to_markdown_recommendations_and_remediation_merged():
    from nuguard.behavior.models import Recommendation
    rec = Recommendation(
        component="booking_agent",
        recommendation_type="system_prompt",
        description="Restrict topic scope",
        rationale="Policy alignment",
        priority="high",
    )
    result = _make_result(recommendations=[rec])
    md = to_markdown(result)
    assert "## Recommendations & Remediation Plan" in md
    assert "## Recommendations" not in md.split("## Recommendations & Remediation Plan")[1]
    assert "## Remediation Plan" not in md
