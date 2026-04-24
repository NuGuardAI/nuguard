"""Red-team report generation — Markdown and JSON.

Public API mirrors :mod:`nuguard.behavior.report` so callers can import
either module with the same interface::

    from nuguard.redteam.report import to_markdown, to_json
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.cli.report_meta import ReportMeta

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_json(
    findings: list,
    meta: "ReportMeta | None" = None,
    remediation_plan: list | None = None,
) -> str:
    """Generate a JSON report string from red-team findings.

    Args:
        findings: List of :class:`~nuguard.models.finding.Finding` objects.
        meta: Optional report metadata.
        remediation_plan: Optional list of ``RemediationArtefact`` objects.

    Returns:
        JSON string.
    """
    from nuguard.cli.report_meta import ReportMeta as _ReportMeta

    if meta is None:
        meta = _ReportMeta()

    payload: dict[str, Any] = {
        "_meta": meta.to_dict(),
        "findings": [f.model_dump() for f in findings],
        "remediation_plan": [a.model_dump() for a in (remediation_plan or [])],
    }
    return json.dumps(payload, indent=2, default=str)


def to_markdown(
    findings: list,
    meta: "ReportMeta | None" = None,
    remediation_plan: list | None = None,
    scenario_records: list | None = None,
) -> str:
    """Render red-team findings as a Markdown report string.

    When *remediation_plan* is supplied (a list of ``RemediationArtefact``
    objects produced by :class:`~nuguard.behavior.remediation.RemediationSynthesizer`),
    a ``## Remediation Plan`` section is appended with concrete, SBOM-node
    specific patches, guardrails and architectural changes grouped by
    component — matching the behavior report's layout.

    When *scenario_records* is supplied (a list of ``ScenarioRecord`` objects
    from the orchestrator), a ``## Scenario Coverage`` table is inserted
    immediately after the report header, before the per-finding detail.

    Args:
        findings: List of :class:`~nuguard.models.finding.Finding` objects.
        meta: Optional report metadata.
        remediation_plan: Optional list of ``RemediationArtefact`` objects.
        scenario_records: Optional list of ``ScenarioRecord`` objects.

    Returns:
        Markdown string.
    """
    from nuguard.cli.report_meta import ReportMeta as _ReportMeta

    if meta is None:
        meta = _ReportMeta()

    lines: list[str] = ["# NuGuard Red-Team Report", ""]
    lines += meta.to_markdown_lines()

    if scenario_records:
        lines += _scenario_coverage_table(scenario_records)

    if not findings:
        lines += ["_No findings — scan complete._", ""]
        return "\n".join(lines)

    from nuguard.models.finding import Severity

    lines += [f"**{len(findings)} finding(s)**", ""]
    for f in sorted(findings, key=lambda x: list(Severity).index(x.severity)):
        sev = f.severity.value.upper() if hasattr(f.severity, "value") else str(f.severity).upper()
        lines += [f"## [{sev}] {f.title}", ""]
        lines += [f.description, ""]
        if f.affected_component:
            lines += [f"**Component:** {f.affected_component}", ""]
        if f.goal_type:
            lines += [f"**Type:** {f.goal_type}", ""]
        # Progress score (1–5 rubric)
        scores = getattr(f, "scores", None) or {}
        progress = scores.get("goal_progress")
        if progress is not None:
            lines += [f"**Progress score:** {progress}/5", ""]
        # One-sentence reasoning from the judge
        reasoning = getattr(f, "reasoning", "") or ""
        if reasoning:
            lines += [f"**Finding reasoning:** {reasoning}", ""]
        # Named success indicator
        success_indicator = getattr(f, "success_indicator", None)
        if success_indicator:
            lines += [f"**Success indicator:** `{success_indicator}`", ""]
        # Evidence quote — exact substring proving the breach
        evidence_quote = getattr(f, "evidence_quote", "") or ""
        if evidence_quote:
            lines += [
                "**Evidence quote:**",
                "> " + evidence_quote.replace("\n", "\n> "),
                "",
            ]
        if f.remediation:
            lines += [f"**Remediation:** {f.remediation}", ""]
        if f.owasp_asi_ref:
            lines += [f"**OWASP ASI:** {f.owasp_asi_ref}", ""]
        if f.owasp_llm_ref:
            lines += [f"**OWASP LLM:** {f.owasp_llm_ref}", ""]
        if f.evidence:
            lines += [
                "**Evidence:**",
                "```",
                _truncate_evidence(f.evidence, limit=2500),
                "```",
                "",
            ]

    if remediation_plan:
        _append_remediation_plan(lines, remediation_plan)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers (not part of the public API)
# ---------------------------------------------------------------------------


def _scenario_coverage_table(scenario_records: list) -> list[str]:
    """Return Markdown lines for the Scenario Coverage summary table.

    One row per executed scenario, sorted by impact score descending.
    Columns: rank, title, goal, finding (YES/no), turns used / budget,
    duration, avg time per turn.

    A summary line below the table shows aggregate stats.
    """
    if not scenario_records:
        return []

    records = sorted(
        scenario_records,
        key=lambda r: (-getattr(r, "impact_score", 0.0), 0 if r.had_finding else 1),
    )

    _GOAL_ABBREV = {
        "DATA_EXFILTRATION": "Data Exfil",
        "PRIVILEGE_ESCALATION": "Priv Esc",
        "PROMPT_DRIVEN_THREAT": "Prompt Threat",
        "POLICY_VIOLATION": "Policy Viol",
        "TOOL_ABUSE": "Tool Abuse",
        "API_ATTACK": "API Attack",
        "MCP_TOXIC_FLOW": "MCP Toxic",
    }

    def _fmt_duration(s: float) -> str:
        if s <= 0:
            return "—"
        return f"{s:.1f}s"

    def _fmt_avg(s: float, turns: int) -> str:
        if s <= 0 or turns <= 0:
            return "—"
        return f"{s / turns:.1f}s"

    lines: list[str] = ["## Scenario Coverage", ""]
    lines.append("| # | Scenario | Goal | Finding | Turns | Duration | Avg/Turn |")
    lines.append("|---|---|---|---|---|---|---|")

    total_duration = 0.0
    total_turns = 0
    findings_count = 0

    for idx, r in enumerate(records, start=1):
        title = r.title[:60] + ("…" if len(r.title) > 60 else "")
        goal = _GOAL_ABBREV.get(r.goal_type, r.goal_type.replace("_", " ").title())
        finding_cell = "**YES**" if r.had_finding else "no"
        turns_used = getattr(r, "turns_used", len(r.steps))
        turns_budget = getattr(r, "turns_budget", 0) or turns_used
        turns_cell = f"{turns_used}/{turns_budget}"
        duration = getattr(r, "duration_s", 0.0)
        dur_cell = _fmt_duration(duration)
        avg_cell = _fmt_avg(duration, turns_used)

        total_duration += duration
        total_turns += turns_used
        if r.had_finding:
            findings_count += 1

        lines.append(
            f"| {idx} | {title} | {goal} | {finding_cell} "
            f"| {turns_cell} | {dur_cell} | {avg_cell} |"
        )

    n = len(records)
    avg_scenario = _fmt_duration(total_duration / n if n else 0)
    avg_turn = _fmt_avg(total_duration, total_turns)
    lines.append("")
    lines.append(
        f"_{n} scenario(s) executed — {findings_count} finding(s). "
        f"Total: {_fmt_duration(total_duration)} | "
        f"Avg per scenario: {avg_scenario} | Avg per turn: {avg_turn}_"
    )
    lines.append("")
    return lines


def _append_remediation_plan(lines: list[str], remediation_plan: list) -> None:
    """Append a Remediation Plan section to *lines*, grouped by SBOM node."""
    from nuguard.behavior.report import _render_artefact

    lines.append("## Remediation Plan")
    lines.append("")
    lines.append(
        "Concrete, SBOM-node-specific remediations generated from the findings "
        "above. Apply in priority order."
    )
    lines.append("")

    by_component: dict[str, list] = {}
    for art in remediation_plan:
        by_component.setdefault(art.component, []).append(art)

    for comp, arts in by_component.items():
        lines.append(f"### {comp}")
        lines.append("")
        for art in arts:
            _render_artefact(lines, art)


def _truncate_evidence(text: str, *, limit: int = 2500) -> str:
    """Trim *text* to ``limit`` chars at a newline/word boundary."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    window = max(200, limit // 5)
    last_nl = cut.rfind("\n", limit - window)
    if last_nl != -1:
        return cut[:last_nl] + "\n… (truncated)"
    last_sp = cut.rfind(" ", limit - 80)
    if last_sp != -1:
        return cut[:last_sp] + " … (truncated)"
    return cut + "… (truncated)"
