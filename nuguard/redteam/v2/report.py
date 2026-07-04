"""Phase 7 — v2 report: findings + coverage gaps + transferability + controls.

Renders the run as Markdown and as a JSON dict.  SARIF reuses the shared
:func:`nuguard.output.sarif_generator.generate_sarif`.  The report deliberately
surfaces *coverage gaps* (what was skipped/blocked and why) alongside findings, so
a reader sees both what was tested and what was not.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nuguard.models.finding import Finding, Severity

if TYPE_CHECKING:
    from nuguard.redteam.v2.planning.coverage_matrix import CoverageMatrix

_SEV_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


@dataclass
class ReportSummary:
    total_findings: int
    by_severity: dict[str, int] = field(default_factory=dict)
    needs_review: int = 0
    transferable: int = 0
    coverage: dict[str, object] | None = None


def summarize(findings: list[Finding], coverage: "CoverageMatrix | None" = None) -> ReportSummary:
    by_sev = Counter(f.severity.value for f in findings)
    return ReportSummary(
        total_findings=len(findings),
        by_severity={s.value: by_sev.get(s.value, 0) for s in _SEV_ORDER if by_sev.get(s.value)},
        needs_review=sum(1 for f in findings if f.scores.get("needs_human_review")),
        transferable=sum(1 for f in findings if f.scores.get("transferable")),
        coverage=coverage.summary() if coverage else None,
    )


def build_json_report(
    findings: list[Finding],
    *,
    coverage: "CoverageMatrix | None" = None,
    target_url: str = "",
    generated_at: str | None = None,
) -> dict:
    summary = summarize(findings, coverage)
    report: dict = {
        "schema": "nuguard-redteam-v2",
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "target_url": target_url,
        "summary": {
            "total_findings": summary.total_findings,
            "by_severity": summary.by_severity,
            "needs_human_review": summary.needs_review,
            "transferable": summary.transferable,
        },
        "findings": [f.model_dump(mode="json") for f in findings],
        "control_validation": _control_map(findings),
        "transferability_clusters": _clusters(findings),
    }
    if coverage is not None:
        report["coverage"] = coverage.summary()
        report["coverage_gaps"] = [
            {"dimension": _dim_of(coverage, e.key), "key": e.key, "status": e.status.value, "reason": e.reason}
            for e in coverage.gaps()
        ]
    return report


def build_markdown_report(
    findings: list[Finding],
    *,
    coverage: "CoverageMatrix | None" = None,
    target_url: str = "",
    generated_at: str | None = None,
) -> str:
    summary = summarize(findings, coverage)
    lines: list[str] = []
    lines.append("# NuGuard Red-Team v2 Report")
    lines.append("")
    lines.append(f"- Target: `{target_url or 'n/a'}`")
    lines.append(f"- Generated: {generated_at or datetime.now(UTC).isoformat()}")
    lines.append(f"- Findings: **{summary.total_findings}**"
                 f" | needs review: {summary.needs_review}"
                 f" | transferable: {summary.transferable}")
    if summary.by_severity:
        sev = ", ".join(f"{k}: {v}" for k, v in summary.by_severity.items())
        lines.append(f"- Severity: {sev}")
    lines.append("")

    # Findings
    lines.append("## Findings")
    if not findings:
        lines.append("_No confirmed findings._")
    for f in findings:
        lines.append("")
        lines.append(f"### [{f.severity.value.upper()}] {f.title}")
        lines.append(f"- ID: `{f.finding_id}` | family: `{f.goal_type}`")
        if f.policy_clauses_violated:
            lines.append(f"- Policy clauses: {', '.join(f.policy_clauses_violated)}")
        if f.sbom_path:
            lines.append(f"- Affected nodes: {', '.join(f.sbom_path)}")
        refs = [r for r in (f.owasp_llm_ref, f.owasp_asi_ref, f.mitre_atlas_technique) if r]
        if refs:
            lines.append(f"- Mapped: {', '.join(refs)}")
        if f.reasoning:
            lines.append(f"- Reasoning: {f.reasoning}")
        if f.evidence:
            lines.append(f"- Evidence: {f.evidence}")
        if f.remediation:
            lines.append(f"- Remediation: {f.remediation}")
        if f.references:
            lines.append(f"- Sources: {', '.join(f.references)}")

    # Transferability clusters
    clusters = _clusters(findings)
    if clusters:
        lines.append("")
        lines.append("## Transferability Clusters")
        for cid, ids in clusters.items():
            lines.append(f"- `{cid}`: {len(ids)} findings ({', '.join(ids)})")

    # Control validation map
    controls = _control_map(findings)
    if controls:
        lines.append("")
        lines.append("## Control Validation")
        for control, ids in controls.items():
            lines.append(f"- **{control}** — failed in {len(ids)} finding(s)")

    # Coverage gaps
    if coverage is not None:
        gaps = coverage.gaps()
        lines.append("")
        lines.append("## Coverage")
        lines.append(f"- Objectives generated: {coverage.total_objectives}")
        lines.append(f"- Coverage gaps: {len(gaps)}")
        gap_reasons = Counter(e.reason for e in gaps)
        for reason, n in gap_reasons.most_common():
            lines.append(f"  - {reason}: {n}")

    lines.append("")
    return "\n".join(lines)


# ── helpers ──────────────────────────────────────────────────────────────────────
def _clusters(findings: list[Finding]) -> dict[str, list[str]]:
    clusters: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        if f.scores.get("transferable") and f.chain_id:
            clusters[f.chain_id].append(f.finding_id)
    return {k: v for k, v in clusters.items() if len(v) >= 2}


def _control_map(findings: list[Finding]) -> dict[str, list[str]]:
    controls: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        if not f.remediation:
            continue
        # remediation is "Validate/strengthen controls: a; b; c"
        body = f.remediation.split(":", 1)[1] if ":" in f.remediation else ""
        for control in (c.strip() for c in body.split(";")):
            if control:
                controls[control].append(f.finding_id)
    return dict(controls)


def _dim_of(coverage: "CoverageMatrix", key: str) -> str:
    if key in coverage.sbom_nodes:
        return "sbom_node"
    if key in coverage.policy_clauses:
        return "policy_clause"
    return "technique_family"
