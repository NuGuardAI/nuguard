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
    scan_outcome: str = "no_findings",
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
        "scan_outcome": scan_outcome,
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

    # --- Summary section -------------------------------------------------------
    from nuguard.models.finding import Severity

    lines += ["## Summary", ""]
    if meta.scan_profile:
        lines += [f"- **Scan Profile**: {meta.scan_profile}", ""]
    _WEIGHTS = {"CRITICAL": 100.0, "HIGH": 70.0, "MEDIUM": 40.0, "LOW": 10.0, "INFO": 0.0}
    if findings:
        _scores = [
            _WEIGHTS.get(
                f.severity.value.upper() if hasattr(f.severity, "value") else str(f.severity).upper(),
                10.0,
            )
            for f in findings
        ]
        _risk_score = round(sum(_scores) / len(_scores), 1)
    else:
        _risk_score = 0.0
    lines += [f"- **Overall Risk Score**: {_risk_score:.1f} / 100", ""]
    total = len(findings)
    lines += [f"- **Total Findings**: {total}", ""]
    if findings:
        sev_counts: dict[str, int] = {}
        for f in findings:
            sev = f.severity.value.upper() if hasattr(f.severity, "value") else str(f.severity).upper()
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
        sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
        sev_parts = [f"{s}: {sev_counts[s]}" for s in sev_order if s in sev_counts]
        if sev_parts:
            lines += [f"- **By Severity**: {' | '.join(sev_parts)}", ""]
    if meta.finding_triggers:
        trigger_parts = [f"{k}={'on' if v else 'off'}" for k, v in meta.finding_triggers.items()]
        lines += [f"- **Finding Triggers**: {', '.join(trigger_parts)}", ""]
    if scenario_records:
        lines += _attack_coverage_summary(scenario_records)
    # ---------------------------------------------------------------------------

    if scenario_records:
        lines += _scenario_coverage_table(scenario_records)

    if not findings:
        lines += ["_No findings — scan complete._", ""]
        return "\n".join(lines)

    for f in sorted(findings, key=lambda x: list(Severity).index(x.severity)):
        sev = f.severity.value.upper() if hasattr(f.severity, "value") else str(f.severity).upper()
        lines += [f"## [{sev}] {f.title}", ""]
        lines += [f.description, ""]
        if f.title.startswith("Inject Success Signal — "):
            lines += ["**Confidence:** Low — keyword match only, verify manually", ""]
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
        if f.owasp_llm_ref:
            lines += [f"**OWASP LLM:** {f.owasp_llm_ref}", ""]
        golden_ids = getattr(f, "golden_ids", None) or []
        golden_name = getattr(f, "golden_name", None)
        golden_excerpt = getattr(f, "golden_data_excerpt", None)
        if golden_ids or golden_name or golden_excerpt:
            lines += ["**Golden Data Baseline** (authenticated test account's own data, "
                      "used to distinguish expected self-returns from genuine cross-account leakage):", ""]
            if golden_name:
                lines += [f"- Name: `{golden_name}`"]
            if golden_ids:
                lines += [f"- ID(s): {', '.join(f'`{i}`' for i in golden_ids)}"]
            if golden_excerpt:
                lines += ["", "```", golden_excerpt, "```"]
            lines += [""]
        lines += _render_hit_turns(f)

    if remediation_plan:
        _append_remediation_plan(lines, remediation_plan)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers (not part of the public API)
# ---------------------------------------------------------------------------


def _attack_coverage_summary(scenario_records: list) -> list[str]:
    """Return Markdown lines for the Attack Coverage bullets + breakdown table in Summary.

    Emits:
    - **Attack Coverage**: N goal type(s)
    - **Coverage**: XX% (N/N scenarios completed)

    Followed by a per-goal-type breakdown table with a Not Tested column.
    Goal types are derived from actual scenario records (no hardcoded universe).
    Not Tested = chain_status in {skipped, similar_miss, failed, aborted}.
    """
    _GOAL_LABEL = {
        "DATA_EXFILTRATION": "Data Exfil",
        "PRIVILEGE_ESCALATION": "Priv Esc",
        "PROMPT_DRIVEN_THREAT": "Prompt Threat",
        "POLICY_VIOLATION": "Policy Viol",
        "TOOL_ABUSE": "Tool Abuse",
        "API_ATTACK": "API Attack",
        "MCP_TOXIC_FLOW": "MCP Toxic",
    }
    _NOT_TESTED = {"skipped", "similar_miss", "failed", "aborted"}

    # Accumulate per-goal-type counts
    goal_data: dict[str, dict[str, int]] = {}
    for r in scenario_records:
        gt = getattr(r, "goal_type", None) or "UNKNOWN"
        status = getattr(r, "chain_status", "completed") or "completed"
        if gt not in goal_data:
            goal_data[gt] = {"total": 0, "not_tested": 0}
        goal_data[gt]["total"] += 1
        if status in _NOT_TESTED:
            goal_data[gt]["not_tested"] += 1

    total_all = sum(d["total"] for d in goal_data.values())
    total_not_tested = sum(d["not_tested"] for d in goal_data.values())
    total_completed = total_all - total_not_tested
    overall_pct = round(total_completed / total_all * 100) if total_all else 0

    # Sort by scenario count descending
    sorted_types = sorted(goal_data.items(), key=lambda x: -x[1]["total"])

    lines: list[str] = [
        f"- **Attack Coverage**: {len(sorted_types)} goal type(s)",
        "",
        f"- **Coverage**: {overall_pct}% ({total_completed}/{total_all} scenarios completed)",
        "",
        "| Goal Type | Scenarios | Not Tested | Coverage |",
        "|---|---|---|---|",
    ]
    for gt, data in sorted_types:
        label = _GOAL_LABEL.get(gt, gt.replace("_", " ").title())
        total = data["total"]
        not_tested = data["not_tested"]
        pct = round((total - not_tested) / total * 100) if total else 0
        lines.append(f"| {label} | {total} | {not_tested} | {pct}% |")
    lines.append("")
    return lines


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


def _render_hit_turns(f: Any) -> list[str]:
    """Return Markdown lines showing only the HIT turns from a finding's attack_steps.

    WARMUP and DISCOVER turns are excluded — they are non-adversarial and never
    constitute a finding. MISS turns (succeeded=False) are excluded so evidence
    focuses on what actually happened, not the failed attempts.

    Falls back to the raw f.evidence code-block when no HIT turns exist (e.g.
    policy-violation findings whose trigger step may not match success_signal).
    """
    _NON_ADVERSARIAL = ("WARMUP", "DISCOVER")
    attack_steps: list[dict] = getattr(f, "attack_steps", None) or []
    hit_steps = [
        (i + 1, s)
        for i, s in enumerate(attack_steps)
        if s.get("succeeded") and s.get("step_type") not in _NON_ADVERSARIAL
    ]
    if not hit_steps:
        if getattr(f, "evidence", None):
            return [
                "**Evidence:**",
                "```",
                _truncate_evidence(f.evidence, limit=2500),
                "```",
                "",
            ]
        return []

    lines: list[str] = ["**Evidence — hit turn(s):**", ""]
    for step_num, step in hit_steps:
        stype = step.get("step_type", "?")
        lines.append(f"**Turn {step_num} ({stype} ✅)**")
        lines.append("")
        target = step.get("target_path")
        if target:
            method = step.get("method", "POST")
            status = step.get("status_code")
            attacker_label = f"{method} {target}"
            if status is not None:
                attacker_label += f" → HTTP {status}"
            lines.append(f"**Attacker:** {attacker_label}")
        else:
            payload = (step.get("payload") or "").strip()
            if payload:
                lines.append(f"**Attacker:** {payload}")
        response = (step.get("response") or "").strip()
        if response:
            lines.append(f"**Agent:** {_truncate_evidence(response, limit=1500)}")
        llm_evidence = (step.get("llm_eval_evidence") or "").strip()
        if llm_evidence:
            conf = step.get("llm_eval_confidence") or ""
            conf_label = f" ({conf})" if conf else ""
            lines.append(f"**LLM eval{conf_label}:** {llm_evidence}")
        lines.append("")
    return lines


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
