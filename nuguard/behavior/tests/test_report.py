"""Unit tests for nuguard/behavior/report.py."""
from __future__ import annotations

import json

from nuguard.behavior.models import (
    BehaviorAnalysisResult,
    BehaviorCoverage,
    IntentProfile,
    Recommendation,
    ScenarioResult,
)
from nuguard.behavior.report import to_json, to_markdown
from nuguard.cli.report_meta import ReportMeta
from nuguard.output.report_shared import _norm_sev

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(**kwargs) -> BehaviorAnalysisResult:
    defaults: dict = dict(intent=IntentProfile(app_purpose="Test App"))
    defaults.update(kwargs)
    return BehaviorAnalysisResult(**defaults)


def _make_scenario_result(
    name: str = "my_scenario",
    scenario_type: str = "component_coverage",
    score: float = 3.8,
    verdicts: list[dict] | None = None,
    deviations: list[dict] | None = None,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id="test-id",
        scenario_name=name,
        scenario_type=scenario_type,
        overall_score=score,
        total_turns=3,
        coverage_pct=1.0,
        verdicts=verdicts or [],
        deviations=deviations or [],
    )


# ---------------------------------------------------------------------------
# _norm_sev
# ---------------------------------------------------------------------------


def test_norm_sev_plain_string():
    assert _norm_sev("high") == "HIGH"
    assert _norm_sev("CRITICAL") == "CRITICAL"
    assert _norm_sev("medium") == "MEDIUM"


def test_norm_sev_enum_style():
    assert _norm_sev("Severity.HIGH") == "HIGH"
    assert _norm_sev("SEVERITY.CRITICAL") == "CRITICAL"
    assert _norm_sev("severity.low") == "LOW"


# ---------------------------------------------------------------------------
# to_json
# ---------------------------------------------------------------------------


def test_to_json_top_level_keys():
    result = _make_result()
    output = to_json(result)
    data = json.loads(output)
    for key in ("run_id", "created_at", "scan_outcome", "overall_risk_score",
                 "coverage_percentage", "intent_alignment_score", "intent",
                 "static_findings", "dynamic_findings", "scenario_results",
                 "coverage", "recommendations", "remediation_plan"):
        assert key in data, f"Missing key: {key}"


def test_to_json_with_findings():
    result = _make_result(
        static_findings=[{"severity": "high", "title": "Bad thing"}],
    )
    data = json.loads(to_json(result))
    assert data["overall_risk_score"] == 80.0
    assert len(data["static_findings"]) == 1


def test_to_json_with_meta():
    from types import SimpleNamespace
    result = _make_result()
    meta = SimpleNamespace(config_path="nuguard.yaml", sbom_path="app.sbom.json", policy_path="policy.yaml")
    data = json.loads(to_json(result, meta=meta))
    assert data["meta"]["config_path"] == "nuguard.yaml"
    assert data["meta"]["sbom_path"] == "app.sbom.json"


# ---------------------------------------------------------------------------
# to_markdown — structure
# ---------------------------------------------------------------------------


def test_to_markdown_contains_summary_header():
    md = to_markdown(_make_result())
    assert "# Behavior Analysis Report" in md
    assert "## Summary" in md


def test_to_markdown_shows_app_purpose():
    result = _make_result(intent=IntentProfile(app_purpose="Banking Assistant"))
    md = to_markdown(result)
    assert "Banking Assistant" in md


def test_to_markdown_coverage_line():
    cov = [
        BehaviorCoverage(component_name="agent1", node_type="AGENT", exercised=True),
        BehaviorCoverage(component_name="tool1", node_type="TOOL", exercised=False),
    ]
    result = _make_result(coverage=cov)
    md = to_markdown(result)
    assert "50%" in md
    assert "1/2" in md


def test_to_markdown_not_exercised_in_summary():
    cov = [
        BehaviorCoverage(component_name="agent1", node_type="AGENT", exercised=True),
        BehaviorCoverage(component_name="tool_x", node_type="TOOL", exercised=False),
    ]
    result = _make_result(coverage=cov)
    md = to_markdown(result)
    assert "Not Exercised" in md
    assert "`tool_x`" in md


def test_to_markdown_not_exercised_annotates_refusal_reason():
    """When escalation classified a refusal reason, the flat list is replaced
    by name + reason pairs (nuguard.behavior.escalate_on_refusal)."""
    cov = [
        BehaviorCoverage(component_name="agent1", node_type="AGENT", exercised=True),
        BehaviorCoverage(
            component_name="Fill Pdf Form", node_type="TOOL", exercised=False,
            refusal_reason="nl_routing_miss",
        ),
        BehaviorCoverage(
            component_name="Complete Job", node_type="TOOL", exercised=False,
            refusal_reason="missing_precondition",
        ),
        BehaviorCoverage(
            component_name="Search Patients By Condition", node_type="TOOL", exercised=False,
            refusal_reason="systemic_deflection",
        ),
    ]
    result = _make_result(coverage=cov)
    md = to_markdown(result)
    assert "Not Exercised" in md
    assert "`Fill Pdf Form` — nl_routing_miss" in md
    assert "`Complete Job` — missing_precondition" in md
    assert "`Search Patients By Condition` — systemic_deflection" in md


def test_to_markdown_not_exercised_falls_back_to_flat_when_no_classification():
    """No component has a refusal_reason (escalate_on_refusal off, the
    default) — the flat, undifferentiated list is preserved exactly as
    before so existing report snapshots are unaffected."""
    cov = [
        BehaviorCoverage(component_name="tool_x", node_type="TOOL", exercised=False),
        BehaviorCoverage(component_name="tool_y", node_type="TOOL", exercised=False),
    ]
    result = _make_result(coverage=cov)
    md = to_markdown(result)
    assert "`tool_x`, `tool_y`" in md or "`tool_y`, `tool_x`" in md
    assert "—" not in md.split("Not Exercised")[1].split("\n")[0]


# ---------------------------------------------------------------------------
# to_markdown — scenarios_skipped / scenarios_deprioritized
# ---------------------------------------------------------------------------


def test_to_markdown_scenarios_deprioritized_shown_separately():
    result = _make_result(
        scenarios_skipped=["scenario_a", "scenario_b"],
        scenarios_deprioritized=["scenario_b"],
    )
    md = to_markdown(result)
    assert "Scenarios Not Run" in md
    assert "`scenario_a`" in md
    assert "Scenarios Deprioritized" in md
    assert "`scenario_b`" in md
    # scenario_b is deprioritized, not a plain cap-skip, so it shouldn't
    # also appear in the plain "Scenarios Not Run" line.
    not_run_line = next(line for line in md.splitlines() if "Scenarios Not Run" in line)
    assert "scenario_b" not in not_run_line


def test_to_markdown_no_deprioritized_section_when_empty():
    result = _make_result(scenarios_skipped=["scenario_a"], scenarios_deprioritized=[])
    md = to_markdown(result)
    assert "Scenarios Deprioritized" not in md


# ---------------------------------------------------------------------------
# to_markdown — scenarios_skipped
# ---------------------------------------------------------------------------


def test_to_markdown_scenarios_skipped_shown():
    result = _make_result(scenarios_skipped=["scenario_a", "scenario_b"])
    md = to_markdown(result)
    assert "Scenarios Not Run" in md
    assert "`scenario_a`" in md
    assert "`scenario_b`" in md


def test_to_markdown_no_skipped_section_when_empty():
    result = _make_result(scenarios_skipped=[])
    md = to_markdown(result)
    assert "Scenarios Not Run" not in md


# ---------------------------------------------------------------------------
# to_markdown — turn verdict table columns
# ---------------------------------------------------------------------------


def test_to_markdown_turn_table_uses_current_dimensions():
    verdicts = [
        {
            "turn": 1,
            "verdict": "FAIL",
            "scores": {
                "component_invoked": 1.0,
                "response_validity": 2.0,
                "topic_alignment": 3.0,
            },
            "overall_score": 1.8,
            "gaps": ["agent did not respond"],
        }
    ]
    sr = _make_scenario_result(score=1.8, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)

    # Correct column headers
    assert "| Comp |" in md
    assert "| Validity |" in md
    assert "| Alignment |" in md
    assert "| Score |" in md

    # Old stale column headers must not appear
    assert "Intent" not in md.split("## Dynamic Analysis")[1]
    assert "Compliance" not in md.split("## Dynamic Analysis")[1]
    assert "Escalation" not in md.split("## Dynamic Analysis")[1]

    # Score values rendered
    assert "1.0" in md
    assert "2.0" in md
    assert "3.0" in md
    assert "1.80" in md


def test_to_markdown_all_pass_shows_no_table():
    verdicts = [
        {
            "turn": 1,
            "verdict": "PASS",
            "scores": {"component_invoked": 5.0, "response_validity": 5.0, "topic_alignment": 5.0},
            "overall_score": 5.0,
            "gaps": [],
        }
    ]
    sr = _make_scenario_result(score=5.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "_All turns passed._" in md


# ---------------------------------------------------------------------------
# to_markdown — covered components per scenario
# ---------------------------------------------------------------------------


def test_to_markdown_shows_covered_components():
    verdicts = [
        {
            "turn": 1,
            "verdict": "PASS",
            "scores": {"component_invoked": 5.0, "response_validity": 5.0, "topic_alignment": 5.0},
            "overall_score": 5.0,
            "gaps": [],
            "agents_mentioned": ["MyAgent"],
            "tools_mentioned": ["get_balance", "transfer_funds"],
        }
    ]
    sr = _make_scenario_result(score=5.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "Covered components" in md
    assert "MyAgent" in md
    assert "get_balance" in md
    assert "transfer_funds" in md


def test_to_markdown_no_covered_section_when_none_mentioned():
    verdicts = [
        {
            "turn": 1,
            "verdict": "FAIL",
            "scores": {},
            "overall_score": 1.0,
            "gaps": [],
            "agents_mentioned": [],
            "tools_mentioned": [],
        }
    ]
    sr = _make_scenario_result(score=1.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "Covered components" not in md


def test_to_markdown_covered_components_deduplicated():
    verdicts = [
        {
            "turn": 1, "verdict": "PASS", "scores": {}, "overall_score": 4.0, "gaps": [],
            "agents_mentioned": ["AgentA"], "tools_mentioned": ["tool1"],
        },
        {
            "turn": 2, "verdict": "PASS", "scores": {}, "overall_score": 4.0, "gaps": [],
            "agents_mentioned": ["AgentA"], "tools_mentioned": ["tool1", "tool2"],
        },
    ]
    sr = _make_scenario_result(score=4.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    # AgentA and tool1 should appear only once each in the covered line
    dynamic_section = md.split("## Dynamic Analysis")[1]
    assert dynamic_section.count("Covered components") == 1
    assert dynamic_section.count("AgentA") == 1
    assert dynamic_section.count("tool1") == 1


# ---------------------------------------------------------------------------
# to_markdown — recommendations
# ---------------------------------------------------------------------------


def test_to_markdown_recommendations_section():
    rec = Recommendation(
        component="AgentX",
        recommendation_type="system_prompt",
        description="Remove restricted topic",
        rationale="BA-001 finding",
        priority="high",
    )
    result = _make_result(recommendations=[rec])
    md = to_markdown(result)
    assert "## Recommendations" in md
    assert "Remove restricted topic" in md
    assert "BA-001 finding" in md


def test_to_markdown_summary_count_reconciliation_table():
    result = _make_result(
        static_findings=[{"finding_id": "F-1", "severity": "high", "title": "s1", "description": "d"}],
        dynamic_findings=[
            {
                "finding_id": "F-2",
                "severity": "medium",
                "title": "d1",
                "description": "d",
                "finding_type": "CAPABILITY_GAP",
                "occurrence_count": 3,
            }
        ],
        gap_aggregation_stats={"raw_gap_observations": 3, "unique_gap_observations": 2},
    )
    md = to_markdown(result)
    assert "| Count Bucket | Value |" in md
    assert "| Unique findings (summary) | 2 |" in md
    assert "| Raw gap observations | 3 (text-deduped to 2) |" in md


def test_to_markdown_uses_deviation_evidence_heading_not_findings_heading():
    verdicts = [
        {
            "turn": 1,
            "verdict": "FAIL",
            "scores": {},
            "overall_score": 1.0,
            "gaps": ["missing detail"],
            "deviations": [{"deviation_type": "capability_gap", "description": "desc", "severity": "medium"}],
        }
    ]
    sr = _make_scenario_result(score=1.0, verdicts=verdicts, deviations=[{"deviation_type": "capability_gap"}])
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "## Deviation Evidence (per-turn)" in md
    assert "### [MEDIUM] capability_gap" not in md
    assert "**[MEDIUM] capability_gap**" in md


def test_to_markdown_uses_llm_authored_deviation_remediation_over_template():
    verdicts = [
        {
            "turn": 1,
            "verdict": "FAIL",
            "scores": {},
            "overall_score": 1.0,
            "gaps": ["No FAQ answer was retrieved for the wheelchair assistance question."],
            "deviations": [
                {
                    "deviation_type": "capability_gap",
                    "description": "No FAQ answer was retrieved for the wheelchair assistance question.",
                    "severity": "medium",
                    "remediation": "Add a wheelchair-assistance entry to the FAQ knowledge base.",
                }
            ],
        }
    ]
    sr = _make_scenario_result(score=1.0, verdicts=verdicts, deviations=[{"deviation_type": "capability_gap"}])
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "Add a wheelchair-assistance entry to the FAQ knowledge base." in md
    assert "Review the agent's system prompt and tool configuration" not in md


def test_to_markdown_gap_summary_renders_remediation_and_fallback():
    result = _make_result(
        dynamic_findings=[
            {
                "finding_id": "G-1",
                "severity": "medium",
                "title": "Capability Gap: faq_retriever",
                "description": "desc",
                "finding_type": "CAPABILITY_GAP",
                "affected_component": "faq_retriever",
                "occurrence_count": 3,
                "remediation": "Add a wheelchair-assistance entry to the FAQ knowledge base.",
            },
            {
                "finding_id": "G-2",
                "severity": "medium",
                "title": "Intent Misalignment: doc_agent",
                "description": "desc",
                "finding_type": "INTENT_MISALIGNMENT",
                "affected_component": "doc_agent",
                "occurrence_count": 2,
            },
        ],
        gap_aggregation_stats={
            "raw_gap_observations": 5,
            "unique_gap_observations": 4,
            "min_occurrences_threshold": 1,
        },
    )
    md = to_markdown(result)
    assert "## Behavioral Gap Summary" in md
    assert "| Component | Occurrences | Sample Gaps | Remediation |" in md
    # Explicit remediation is rendered verbatim.
    assert "Add a wheelchair-assistance entry to the FAQ knowledge base." in md
    # Missing remediation falls back to the per-type guidance template.
    assert "Align doc_agent system prompt with application's stated purpose" in md


def test_to_markdown_gap_summary_empty_remediation_falls_back():
    result = _make_result(
        dynamic_findings=[
            {
                "finding_id": "G-3",
                "severity": "medium",
                "title": "Tool Chain Broken: scraper",
                "description": "desc",
                "finding_type": "TOOL_CHAIN_BROKEN",
                "affected_component": "scraper",
                "occurrence_count": 1,
                "remediation": "",
            }
        ],
        gap_aggregation_stats={"min_occurrences_threshold": 1},
    )
    md = to_markdown(result)
    assert "Repair broken tool invocation chain in scraper" in md


def test_to_markdown_gap_summary_escapes_pipe_in_remediation():
    """Remediation text containing | must not break the Markdown table."""
    result = _make_result(
        dynamic_findings=[
            {
                "finding_id": "G-4",
                "severity": "medium",
                "title": "Capability Gap: router",
                "description": "desc",
                "finding_type": "CAPABILITY_GAP",
                "affected_component": "router",
                "occurrence_count": 1,
                "remediation": "Route A | Route B | Route C",
            }
        ],
        gap_aggregation_stats={"min_occurrences_threshold": 1},
    )
    md = to_markdown(result)
    # The pipe must be escaped so the table stays valid (4 columns).
    assert "Route A \\| Route B \\| Route C" in md


def test_to_markdown_gap_summary_escapes_newline_in_remediation():
    """Remediation text containing newlines must not produce extra table rows."""
    result = _make_result(
        dynamic_findings=[
            {
                "finding_id": "G-5",
                "severity": "medium",
                "title": "Intent Misalignment: agent",
                "description": "desc",
                "finding_type": "INTENT_MISALIGNMENT",
                "affected_component": "agent",
                "occurrence_count": 1,
                "remediation": "Step one.\nStep two.\nStep three.",
            }
        ],
        gap_aggregation_stats={"min_occurrences_threshold": 1},
    )
    md = to_markdown(result)
    # Newlines should be collapsed into the cell, not create extra rows.
    assert "Step one. Step two. Step three." in md
    # Ensure no bare newline split the remediation across table rows.
    assert "Step one.\n" not in md.split("## Dynamic")[0]


def test_to_markdown_gap_summary_handles_non_string_remediation():
    """Non-string remediation values (dict/list) must not crash the renderer."""
    result = _make_result(
        dynamic_findings=[
            {
                "finding_id": "G-6",
                "severity": "medium",
                "title": "Tool Chain Broken: svc",
                "description": "desc",
                "finding_type": "TOOL_CHAIN_BROKEN",
                "affected_component": "svc",
                "occurrence_count": 1,
                "remediation": {"step": "fix the chain"},
            }
        ],
        gap_aggregation_stats={"min_occurrences_threshold": 1},
    )
    # Must not raise AttributeError from .strip() on a dict.
    md = to_markdown(result)
    assert "Behavioral Gap Summary" in md


def test_to_markdown_covered_components_tagged_matched_unmatched():
    verdicts = [
        {
            "turn": 1,
            "verdict": "PASS",
            "scores": {},
            "overall_score": 4.5,
            "gaps": [],
            "agents_mentioned": ["Loan Application Agent"],
            "tools_mentioned": ["UnknownTool"],
        }
    ]
    sr = _make_scenario_result(score=4.5, verdicts=verdicts)
    result = _make_result(
        scenario_results=[sr],
        coverage=[
            BehaviorCoverage(component_name="loan_application_agent", node_type="AGENT", exercised=True),
        ],
    )
    md = to_markdown(result)
    assert "Loan Application Agent (matched)" in md
    assert "UnknownTool (unmatched)" in md


def test_to_markdown_renders_effective_endpoint_per_scenario():
    verdicts = [
        {
            "turn": 1,
            "verdict": "PASS",
            "scores": {},
            "overall_score": 4.0,
            "gaps": [],
            "effective_endpoint": "/api/agent",
        }
    ]
    sr = _make_scenario_result(score=4.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])
    md = to_markdown(result)
    assert "**Effective Endpoint**: `/api/agent`" in md


def test_to_markdown_run_profile_section_present():
    result = _make_result(
        run_profile={
            "nuguard_version": "0.0.1",
            "behavior_engine_version": "v1",
            "scenarios_planned": 8,
            "scenarios_executed": 7,
            "scenarios_skipped": 1,
            "total_turns": 39,
            "coverage_turns": 5,
            "llm_used": True,
            "llm_model": "gemini/gemini-2.0-flash",
            "target_fingerprint": "abc123",
            "scenario_types": {"intent_happy_path": 3},
        }
    )
    md = to_markdown(result)
    assert "## Run Profile" in md
    assert "| Scenarios Executed | 7 |" in md
    assert "| Target Fingerprint | abc123 |" in md


def test_to_markdown_comparability_warning_when_scope_changes():
    result = _make_result(
        run_profile={
            "behavior_engine_version": "v1",
            "scenarios_executed": 18,
        }
    )
    meta = ReportMeta(previous_run_profile={"behavior_engine_version": "v1", "scenarios_executed": 7})
    md = to_markdown(result, meta=meta)
    assert "Comparability warning" in md


def test_to_markdown_diagnostics_section_only_in_verbose_mode():
    verdicts = [
        {
            "turn": 1,
            "verdict": "FAIL",
            "user_message": "x" * 1200,
            "agent_response": "y" * 1200,
            "scores": {},
            "overall_score": 1.0,
            "gaps": ["gap1", "gap2", "gap3", "gap4", "gap5"],
        }
    ]
    sr = _make_scenario_result(score=1.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])

    verbose_md = to_markdown(result, meta=ReportMeta(verbose=True))
    non_verbose_md = to_markdown(result, meta=ReportMeta(verbose=False))

    assert "## Diagnostics" in verbose_md
    assert "## Scenario Details" in verbose_md
    assert "## Diagnostics" not in non_verbose_md
    assert "## Scenario Details" not in non_verbose_md


def test_to_json_includes_diagnostics_only_in_verbose_mode():
    verdicts = [
        {
            "turn": 1,
            "verdict": "PASS",
            "user_message": "hello",
            "agent_response": "world",
            "scores": {},
            "overall_score": 4.0,
            "gaps": [],
        }
    ]
    sr = _make_scenario_result(score=4.0, verdicts=verdicts)
    result = _make_result(scenario_results=[sr])

    verbose_json = json.loads(to_json(result, meta=ReportMeta(verbose=True)))
    non_verbose_json = json.loads(to_json(result, meta=ReportMeta(verbose=False)))

    assert "diagnostics" in verbose_json
    assert "scenario_traces" in verbose_json["diagnostics"]
    assert "diagnostics" not in non_verbose_json


def test_to_markdown_remediation_plan_is_separate_section_matching_redteam_format():
    """Remediation Plan must render via the shared renderer as its own '## Remediation
    Plan' -> '### {component}' section, structurally identical to redteam's report,
    rather than folded/nested under a merged Recommendations heading.
    """
    from nuguard.remediation.models import RemediationArtefact, RemediationArtefactType

    rec = Recommendation(
        component="AgentX",
        recommendation_type="system_prompt",
        description="Remove restricted topic",
        rationale="BA-001 finding",
        priority="high",
    )
    artefact = RemediationArtefact(
        finding_ids=["BA-001-abc123"],
        component="AgentX",
        component_type="AGENT",
        artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
        priority="high",
        patch_text="Do not discuss gambling.",
        rationale="Prevents restricted-topic engagement.",
    )
    result = _make_result(recommendations=[rec], remediation_plan=[artefact])
    md = to_markdown(result)

    assert "## Recommendations" in md
    assert "## Recommendations & Remediation Plan" not in md
    assert "## Remediation Plan" in md
    assert "### AgentX" in md
    assert "#### AgentX" not in md
    assert "### Remediation Artefacts" not in md
    # Remediation Plan must come after Recommendations, as two sibling H2 sections.
    assert md.index("## Recommendations") < md.index("## Remediation Plan")


def test_to_markdown_aborted_endpoint_unreachable_shows_note():
    """The pre-flight-abort outcome must render its own explanatory banner
    (previously silently unrecognized, indistinguishable from a normal
    'no findings' report)."""
    result = _make_result(
        static_findings=[{"finding_id": "F-1", "severity": "high", "title": "s1", "description": "d"}],
        dynamic_scan_outcome="aborted_endpoint_unreachable",
    )
    md = to_markdown(result)
    assert "aborted before any scenario ran" in md
    assert "target_endpoint" in md
