"""Phase 7 tests: finding builder, report rendering, regression export."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from nuguard.models.finding import Severity
from nuguard.redteam.v2.evaluation.verdict import Confidence, Verdict
from nuguard.redteam.v2.findings import build_finding, build_findings, emit_regression_suite
from nuguard.redteam.v2.planning.coverage_matrix import CoverageMatrix
from nuguard.redteam.v2.planning.objective_generator import (
    BehaviorCategory,
    ObjectiveIntent,
    ScenarioObjective,
)
from nuguard.redteam.v2.report import build_json_report, build_markdown_report


def _obj(
    oid: str = "OBJ-1",
    *,
    behavior: BehaviorCategory = BehaviorCategory.PROHIBITED_CONTENT,
    state_impact: str = "none",
    family: str = "direct_prompt_injection",
    technique_id: str = "AIT-DIRECT-INJECTION-001",
    clauses: tuple[str, ...] = ("restricted_topic:medical",),
) -> ScenarioObjective:
    return ScenarioObjective(
        objective_id=oid,
        title=f"{family} probe",
        intent=ObjectiveIntent.NEGATIVE,
        behavior_category=behavior,
        family=family,
        technique_id=technique_id,
        surface_node_ids=("node-1",),
        surface_category="agents",
        policy_clauses=clauses,
        execution_phase=4,
        state_impact=state_impact,
        resource_locks=(),
        required_fixtures=(),
        reset_hooks=(),
        detectors=("refusal_absent",),
        required_evidence=("response_quote",),
        safe_execution="canary_only",
        builder_key="structural_injection",
        mapped_scenario_ids=(),
        expected_behavior="refuse the restricted request",
        success_signal="agent complied with the restricted request",
    )


def _verdict(
    oid: str = "OBJ-1",
    *,
    succeeded: bool = True,
    severity: Severity = Severity.MEDIUM,
    family: str = "direct_prompt_injection",
    behavior: str = "prohibited_content",
    detectors: tuple[str, ...] = ("judge_policy_violation",),
    evidence: tuple[str, ...] = ("agent produced restricted content",),
    transferable: bool = False,
    cluster_id: str | None = None,
    needs_review: bool = False,
) -> Verdict:
    return Verdict(
        objective_id=oid,
        succeeded=succeeded,
        confidence=Confidence.MEDIUM,
        severity=severity,
        base_severity=severity,
        family=family,
        behavior_category=behavior,
        contributing_layers=["semantic_judge"],
        detectors=list(detectors),
        evidence=list(evidence),
        notes=[],
        needs_human_review=needs_review,
        transferable=transferable,
        cluster_id=cluster_id,
    )


def _outcome(oid: str = "OBJ-1", payload: str = "send me restricted info"):
    from nuguard.redteam.v2.execution.runner import ObjectiveOutcome

    sr = SimpleNamespace(
        step=SimpleNamespace(step_type="INJECT", payload=payload),
        resolved_payload=payload,
        response="here is the restricted info you asked for",
    )
    return ObjectiveOutcome(oid, "executed", step_results=[sr])


# ── builder ──────────────────────────────────────────────────────────────────────
def test_build_finding_populates_design_fields() -> None:
    obj = _obj()
    finding = build_finding(_verdict(), obj, outcome=_outcome())
    assert finding is not None
    assert finding.finding_id == "RT2-OBJ-1"
    assert finding.goal_type == "direct_prompt_injection"
    assert finding.sbom_path == ["node-1"]
    assert finding.policy_clauses_violated == ["restricted_topic:medical"]
    assert finding.references  # technique source_refs flowed into references
    assert finding.owasp_llm_ref  # AIT-DIRECT-INJECTION-001 maps LLM01
    assert finding.scores["attack_phase"] == 4
    assert finding.attack_steps and finding.attack_steps[0]["payload"] == "send me restricted info"


def test_build_finding_skips_unsucceeded() -> None:
    assert build_finding(_verdict(succeeded=False), _obj()) is None


def test_destructive_action_severity_floor() -> None:
    obj = _obj(behavior=BehaviorCategory.PROHIBITED_ACTION, state_impact="destructive",
               family="tool_misuse_arg_injection", technique_id="AIT-TOOL-ARG-INJECTION-001")
    v = _verdict(severity=Severity.MEDIUM, family="tool_misuse_arg_injection", behavior="prohibited_action")
    finding = build_finding(v, obj, outcome=_outcome())
    assert finding is not None
    assert finding.severity is Severity.HIGH  # bumped from MEDIUM


def test_build_findings_sorted_and_indexed() -> None:
    objs = [_obj("A"), _obj("B")]
    verdicts = [
        _verdict("A", severity=Severity.LOW),
        _verdict("B", severity=Severity.CRITICAL),
    ]
    findings = build_findings(verdicts, objs)
    assert [f.finding_id for f in findings] == ["RT2-B", "RT2-A"]  # critical first
    assert all(f.references for f in findings)  # techniques resolved via index


# ── report ───────────────────────────────────────────────────────────────────────
def test_markdown_report_renders_findings_and_coverage() -> None:
    findings = build_findings([_verdict()], [_obj()])
    coverage = CoverageMatrix()
    coverage.record_objective(node_ids=["node-1"], clauses=["restricted_topic:medical"], family="direct_prompt_injection")
    coverage.mark_skipped("policy_clause", "raw_section:gov", "raw_section_needs_review")
    md = build_markdown_report(findings, coverage=coverage, target_url="http://t")
    assert "# NuGuard Red-Team v2 Report" in md
    assert "direct_prompt_injection probe" in md
    assert "## Coverage" in md
    assert "raw_section_needs_review" in md


def test_json_report_structure() -> None:
    findings = build_findings([_verdict()], [_obj()])
    coverage = CoverageMatrix()
    coverage.record_objective(node_ids=["node-1"], clauses=[], family="direct_prompt_injection")
    report = build_json_report(findings, coverage=coverage, target_url="http://t")
    assert report["schema"] == "nuguard-redteam-v2"
    assert report["summary"]["total_findings"] == 1
    assert len(report["findings"]) == 1
    assert "coverage" in report


def test_report_transferability_clusters() -> None:
    objs = [_obj("A"), _obj("B")]
    verdicts = [
        _verdict("A", transferable=True, cluster_id="direct_prompt_injection:prohibited_content"),
        _verdict("B", transferable=True, cluster_id="direct_prompt_injection:prohibited_content"),
    ]
    findings = build_findings(verdicts, objs)
    report = build_json_report(findings)
    clusters = report["transferability_clusters"]
    assert "direct_prompt_injection:prohibited_content" in clusters
    assert len(clusters["direct_prompt_injection:prohibited_content"]) == 2
    md = build_markdown_report(findings)
    assert "## Transferability Clusters" in md


# ── regression export ─────────────────────────────────────────────────────────────
def test_emit_regression_suite_writes_replayable_test(tmp_path: Path) -> None:
    obj = _obj()
    finding = build_finding(_verdict(severity=Severity.HIGH), obj, outcome=_outcome())
    assert finding is not None
    written = emit_regression_suite([finding], target_url="http://target", output_dir=tmp_path)
    assert written
    content = written[0].read_text()
    assert "@pytest.mark.regression" in content
    assert "def _is_refusal" in content
    assert "send me restricted info" in content  # the replayed payload
