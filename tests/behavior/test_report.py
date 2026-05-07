"""Tests for behavior report generation and risk score calculation."""
from __future__ import annotations

import pytest

from nuguard.behavior.models import BehaviorAnalysisResult, IntentProfile
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
    assert result.overall_risk_score == 10.0


def test_risk_score_single_high():
    result = _make_result(dynamic_findings=[_finding("high")])
    assert result.overall_risk_score == 7.0


def test_risk_score_single_medium():
    result = _make_result(dynamic_findings=[_finding("medium")])
    assert result.overall_risk_score == 4.0


def test_risk_score_single_low():
    result = _make_result(dynamic_findings=[_finding("low")])
    assert result.overall_risk_score == 1.0


def test_risk_score_mixed_critical_and_low():
    # (10 + 1) / (2 * 10) * 10 = 5.5
    result = _make_result(dynamic_findings=[_finding("critical"), _finding("low")])
    assert result.overall_risk_score == 5.5


def test_risk_score_two_highs_below_10():
    # 2 HIGH: (7+7)/(2*10)*10 = 7.0 — no longer capped-but-misleading 10.0
    result = _make_result(dynamic_findings=[_finding("high"), _finding("high")])
    assert result.overall_risk_score == 7.0


def test_risk_score_uses_static_and_dynamic():
    result = _make_result(
        static_findings=[_finding("medium")],
        dynamic_findings=[_finding("high")],
    )
    # (4 + 7) / (2 * 10) * 10 = 5.5
    assert result.overall_risk_score == 5.5


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
