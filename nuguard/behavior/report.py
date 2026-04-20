"""Behavior analysis report generation — Markdown, JSON, and Rich text."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.behavior.models import BehaviorAnalysisResult
    from nuguard.cli.report_meta import ReportMeta

_log = logging.getLogger(__name__)


def to_json(result: "BehaviorAnalysisResult", meta: "ReportMeta | None" = None) -> str:
    """Generate JSON report string.

    Args:
        result: Complete BehaviorAnalysisResult.
        meta: Optional report metadata.

    Returns:
        JSON string.
    """
    data: dict[str, Any] = {
        "run_id": result.run_id,
        "created_at": result.created_at.isoformat(),
        "scan_outcome": result.scan_outcome,
        "overall_risk_score": result.overall_risk_score,
        "coverage_percentage": result.coverage_percentage,
        "intent_alignment_score": result.intent_alignment_score,
        "intent": result.intent.model_dump(),
        "static_findings": result.static_findings,
        "dynamic_findings": result.dynamic_findings,
        "scenario_results": [s.model_dump() for s in result.scenario_results],
        "coverage": [c.model_dump() for c in result.coverage],
        "recommendations": [r.model_dump() for r in result.recommendations],
        "remediation_plan": [a.model_dump() for a in result.remediation_plan],
    }
    if meta is not None:
        data["meta"] = {
            "config_path": str(getattr(meta, "config_path", "") or ""),
            "sbom_path": str(getattr(meta, "sbom_path", "") or ""),
            "policy_path": str(getattr(meta, "policy_path", "") or ""),
        }
    return json.dumps(data, indent=2, default=str)


def _norm_sev(raw: object) -> str:
    """Normalize a severity value to a plain uppercase string (e.g. HIGH, CRITICAL).

    Handles both plain strings (``'high'``, ``'HIGH'``) and enum-style strings
    (``'Severity.HIGH'``, ``'SEVERITY.HIGH'``) produced by ``str(Severity.HIGH)``.
    """
    s = str(raw).upper()
    return s.split(".")[-1] if "." in s else s


def to_markdown(result: "BehaviorAnalysisResult", meta: "ReportMeta | None" = None) -> str:
    """Generate Markdown behavior analysis report.

    Args:
        result: Complete BehaviorAnalysisResult.
        meta: Optional report metadata.

    Returns:
        Markdown string.
    """
    lines: list[str] = []
    lines.append("# Behavior Analysis Report")
    lines.append("")

    # Determine mode
    has_static = bool(result.static_findings)
    has_dynamic = bool(result.dynamic_findings or result.scenario_results)
    if has_static and has_dynamic:
        mode = "static + dynamic"
    elif has_static:
        mode = "static"
    elif has_dynamic:
        mode = "dynamic"
    else:
        mode = "static + dynamic"

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Intent**: {result.intent.app_purpose or 'not determined'}")
    lines.append(f"- **Mode**: {mode}")
    lines.append(f"- **Overall Risk Score**: {result.overall_risk_score:.1f} / 10")
    total_comp = len(result.coverage)
    exercised = sum(1 for c in result.coverage if c.exercised)
    lines.append(f"- **Coverage**: {result.coverage_percentage * 100:.0f}% ({exercised}/{total_comp} components exercised)")
    lines.append(f"- **Intent Alignment Score**: {result.intent_alignment_score:.2f} / 5.0")
    total_findings = len(result.static_findings) + len(result.dynamic_findings)
    lines.append(f"- **Total Findings**: {total_findings}")
    # Severity breakdown
    sev_counts: dict[str, int] = {}
    for f in list(result.static_findings) + list(result.dynamic_findings):
        sev = str(f.get("severity", "unknown")).upper()
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
    if sev_counts:
        sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
        sev_parts = [f"{s}: {sev_counts[s]}" for s in sev_order if s in sev_counts]
        lines.append(f"- **By Severity**: {' | '.join(sev_parts)}")
    lines.append("")

    # Scenario Coverage table — placed right after Summary so it's the first thing readers see
    if result.scenario_results:
        lines.extend(_scenario_coverage_table(result.scenario_results))

    # Static Analysis Findings — grouped by policy rule to avoid per-tool noise
    if result.static_findings:
        lines.append("## Static Analysis Findings")
        lines.append("")
        # Group by (title_prefix, policy_rule) so all tools violating the same rule collapse
        grouped_static: dict[str, list[dict]] = {}
        ungrouped_static: list[dict] = []
        for finding in result.static_findings:
            desc = finding.get("description", "")
            # BA-003: "Policy restricts action 'X', but agent 'Y' has a CALLS edge to tool 'Z'"
            import re as _re
            m = _re.search(r"Policy restricts action '([^']+)'", desc)
            if m:
                rule_key = m.group(1)
                grouped_static.setdefault(rule_key, []).append(finding)
            else:
                ungrouped_static.append(finding)

        # Render grouped (restricted action) findings — one heading per policy rule
        for rule_key, findings_group in grouped_static.items():
            sev = _norm_sev(findings_group[0].get("severity", ""))
            tool_names = [f.get("affected_component", "?") for f in findings_group]
            owasp_llm = "LLM08 – Excessive Agency"
            owasp_asi = "ASI02 – Tool Misuse and Exploitation"
            lines.append(f"### [{sev}] Restricted Action Reachable — '{rule_key}'")
            lines.append("")
            lines.append(f"Policy restricts action '{rule_key}', but {len(tool_names)} tool(s) implementing this action are reachable via CALLS edges:")
            lines.append("")
            for tn in sorted(tool_names):
                # Find remediation for this specific tool
                rem_finding = next((f for f in findings_group if f.get("affected_component") == tn), findings_group[0])
                rem = rem_finding.get("remediation", "")
                lines.append(f"- `{tn}` — {rem}")
            lines.append("")
            lines.append(f"**OWASP LLM:** {owasp_llm}")
            lines.append("")
            lines.append(f"**OWASP ASI:** {owasp_asi}")
            lines.append("")

        # Render ungrouped findings individually (BA-001, BA-004, BA-005, etc.)
        for finding in ungrouped_static:
            title = finding.get("title", "")
            sev = _norm_sev(finding.get("severity", ""))
            comp = finding.get("affected_component", "")
            desc = finding.get("description", "")
            remediation = finding.get("remediation", "")
            owasp_asi_ref = finding.get("owasp_asi_ref") or ""
            owasp_llm_ref = finding.get("owasp_llm_ref") or ""
            lines.append(f"### [{sev}] {title}")
            if comp:
                lines.append(f"**Affected Component:** {comp}")
            lines.append("")
            lines.append(desc)
            lines.append("")
            if remediation:
                lines.append(f"**Remediation:** {remediation}")
                lines.append("")
            if owasp_llm_ref:
                lines.append(f"**OWASP LLM:** {owasp_llm_ref}")
                lines.append("")
            if owasp_asi_ref:
                lines.append(f"**OWASP ASI:** {owasp_asi_ref}")
                lines.append("")

    # Dynamic Analysis Results
    if result.scenario_results:
        lines.append("## Dynamic Analysis Results")
        lines.append("")
        for sr in result.scenario_results:
            lines.append(f"### Scenario: {sr.scenario_name}")
            lines.append(f"- **Type**: {sr.scenario_type}")
            lines.append(f"- **Overall Score**: {sr.overall_score:.2f}")
            lines.append(f"- **Coverage**: {sr.coverage_pct * 100:.0f}%")
            if sr.coverage_turns:
                lines.append(f"- **Turns**: {sr.total_turns} ({sr.coverage_turns} adaptive)")
            else:
                lines.append(f"- **Turns**: {sr.total_turns}")
            lines.append("")

            if sr.verdicts:
                # Only show FAIL/PARTIAL turns (missed or partial coverage)
                non_pass = [v for v in sr.verdicts if v.get("verdict") != "PASS"]
                if not non_pass:
                    lines.append("_All turns passed._")
                    lines.append("")
                else:
                    passed_count = len(sr.verdicts) - len(non_pass)
                    if passed_count > 0:
                        lines.append(
                            f"_Showing {len(non_pass)} missed/partial turn(s) — "
                            f"{passed_count} passing turn(s) omitted._"
                        )
                        lines.append("")
                    lines.append("| Turn | Verdict | Intent | Compliance | Component | Data | Escalation | Gaps |")
                    lines.append("|------|---------|--------|-----------|-----------|------|------------|------|")
                    for v in non_pass:
                        t = v.get("turn", "?")
                        verdict = v.get("verdict", "?")
                        scores = v.get("scores", {})
                        ia = f"{scores.get('intent_alignment', '-'):.0f}" if isinstance(scores.get("intent_alignment"), (int, float)) else "-"
                        bc = f"{scores.get('behavioral_compliance', '-'):.0f}" if isinstance(scores.get("behavioral_compliance"), (int, float)) else "-"
                        cc = f"{scores.get('component_correctness', '-'):.0f}" if isinstance(scores.get("component_correctness"), (int, float)) else "-"
                        dh = f"{scores.get('data_handling', '-'):.0f}" if isinstance(scores.get("data_handling"), (int, float)) else "-"
                        ec = f"{scores.get('escalation_compliance', '-'):.0f}" if isinstance(scores.get("escalation_compliance"), (int, float)) else "-"
                        gaps_raw = v.get("gaps") or []
                        gaps_str = ("; ".join(str(g) for g in gaps_raw[:2]))[:60] or "-"
                        lines.append(f"| {t} | {verdict} | {ia} | {bc} | {cc} | {dh} | {ec} | {gaps_str} |")
                    lines.append("")

                    # Evidence excerpts for FAIL turns only
                    fail_turns = [v for v in non_pass if v.get("verdict") == "FAIL"]
                    evidence_lines: list[str] = []
                    for v in fail_turns[:3]:  # cap at 3
                        t = v.get("turn", "?")
                        agent_resp = v.get("agent_response") or v.get("response") or ""
                        user_msg = v.get("user_message") or v.get("prompt") or ""
                        gaps_raw = v.get("gaps") or []
                        if agent_resp or user_msg:
                            evidence_lines.append(f"_Turn {t}:_")
                            if user_msg:
                                evidence_lines.append(f"> **User:** {str(user_msg)[:200]}")
                            if agent_resp:
                                evidence_lines.append(f"> **Agent:** {str(agent_resp)[:200]}")
                            if gaps_raw:
                                evidence_lines.append(f"> **Gap:** {str(gaps_raw[0])[:150]}")
                            evidence_lines.append("")
                    if evidence_lines:
                        lines.append("**Evidence (FAIL turns):**")
                        lines.append("")
                        lines.extend(evidence_lines)

            if sr.uncovered_agents or sr.uncovered_tools:
                uncovered = sr.uncovered_agents + sr.uncovered_tools
                lines.append(f"**Uncovered components**: {', '.join(uncovered)}")
                lines.append("")

    # Coverage Map
    if result.coverage:
        lines.append("## Coverage Map")
        lines.append("")
        lines.append("| Component | Type | Exercised | Within Policy | Deviations |")
        lines.append("|-----------|------|-----------|---------------|------------|")
        for cov in result.coverage:
            ex = "Yes" if cov.exercised else "No"
            wp = "Yes" if cov.exercised_within_policy else ("No" if cov.exercised else "-")
            devs = len(cov.deviations)
            lines.append(f"| {cov.component_name} | {cov.node_type} | {ex} | {wp} | {devs} |")
        lines.append("")

    # Deviations
    all_deviations: list[dict] = []
    for sr in result.scenario_results:
        for dev in sr.deviations:
            if isinstance(dev, dict):
                all_deviations.append({**dev, "scenario": sr.scenario_name})

    if all_deviations:
        lines.append("## Deviations")
        lines.append("")
        for dev in all_deviations[:20]:  # Cap display
            dtype = dev.get("deviation_type", "unknown")
            desc = dev.get("description", "")
            sev = _norm_sev(dev.get("severity", ""))
            scenario = dev.get("scenario", "")
            lines.append(f"### [{sev}] {dtype}: {desc}")
            if scenario:
                lines.append(f"*Scenario*: {scenario}")
            lines.append("")

    # Dynamic Findings
    if result.dynamic_findings:
        lines.append("## Dynamic Analysis Findings")
        lines.append("")
        for finding in result.dynamic_findings:
            title = finding.get("title", "")
            sev = _norm_sev(finding.get("severity", ""))
            comp = finding.get("affected_component", "")
            desc = finding.get("description", "")
            owasp_asi_ref = finding.get("owasp_asi_ref") or ""
            owasp_llm_ref = finding.get("owasp_llm_ref") or ""
            evidence_quote = finding.get("evidence_quote") or finding.get("evidence") or ""
            reasoning = finding.get("reasoning") or ""
            policy_clause = finding.get("policy_clause") or ""
            # For behavior dynamic findings, description IS the evidence text
            evidence_text = evidence_quote or (desc if desc and desc != title else "")
            lines.append(f"### [{sev}] {title}")
            if comp:
                lines.append(f"**Affected Component:** {comp}")
            lines.append("")
            if reasoning:
                lines.append(f"**Finding:** {reasoning}")
                lines.append("")
            if policy_clause:
                lines.append(f"**Policy Clause:** {policy_clause}")
                lines.append("")
            # Always render description as evidence (even when it matches the title)
            evidence_text = evidence_quote or desc
            if evidence_text:
                lines.append("**Evidence:**")
                lines.append("```")
                lines.append(evidence_text[:500])
                lines.append("```")
                lines.append("")
            if owasp_llm_ref:
                lines.append(f"**OWASP LLM:** {owasp_llm_ref}")
                lines.append("")
            if owasp_asi_ref:
                lines.append(f"**OWASP ASI:** {owasp_asi_ref}")
                lines.append("")

    # Recommendations
    if result.recommendations:
        lines.append("## Recommendations")
        lines.append("")
        for rec in result.recommendations:
            lines.append(f"### [{rec.priority.upper()}] {rec.recommendation_type}: {rec.description}")
            if rec.component and rec.component != "unknown":
                lines.append(f"*Component*: {rec.component}")
            lines.append("")
            lines.append(f"*Rationale*: {rec.rationale}")
            lines.append("")

    # Remediation Plan
    if result.remediation_plan:
        lines.append("## Remediation Plan")
        lines.append("")
        lines.append(
            "Concrete, SBOM-node-specific remediations generated from findings above. "
            "Apply in priority order."
        )
        lines.append("")

        # Group by component
        by_component: dict[str, list] = {}
        for art in result.remediation_plan:
            by_component.setdefault(art.component, []).append(art)

        for comp, arts in by_component.items():
            lines.append(f"### {comp}")
            lines.append("")
            for art in arts:
                _render_artefact(lines, art)

    return "\n".join(lines)


def _scenario_coverage_table(scenario_results: list[Any]) -> list[str]:
    """Build a Scenario Coverage summary table (mirrors redteam report format).

    Args:
        scenario_results: List of ScenarioResult objects.

    Returns:
        List of Markdown lines including the table and a summary footer.
    """
    lines: list[str] = []
    lines.append("## Scenario Coverage")
    lines.append("")
    lines.append("| # | Scenario | Type | Score | Verdict | Finding | Turns | Duration | Avg/Turn |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    total_duration_s = 0.0
    total_turns = 0
    findings_count = 0

    for i, sr in enumerate(scenario_results):
        # Duration: sum latency_ms across all verdict dicts
        duration_ms = sum(v.get("latency_ms", 0) for v in (sr.verdicts or []))
        duration_s = duration_ms / 1000.0
        total_duration_s += duration_s
        total_turns += sr.total_turns or 0

        avg_per_turn = duration_s / sr.total_turns if sr.total_turns else 0.0

        # Verdict from score thresholds (mirrors judge.py constants)
        score = sr.overall_score or 0.0
        if score >= 3.5:
            verdict = "PASS"
        elif score >= 2.0:
            verdict = "PARTIAL"
        else:
            verdict = "FAIL"

        has_finding = bool(sr.deviations) or score < 2.0
        if has_finding:
            findings_count += 1
        finding_cell = "**YES**" if has_finding else "no"

        name = sr.scenario_name or ""
        if len(name) > 45:
            name = name[:45] + "…"
        sc_type = (sr.scenario_type or "").replace("_", " ")

        lines.append(
            f"| {i + 1} | {name} | {sc_type} | {score:.2f} | {verdict} | {finding_cell} | "
            f"{sr.total_turns} | {duration_s:.1f}s | {avg_per_turn:.1f}s |"
        )

    n = len(scenario_results)
    avg_scenario = total_duration_s / n if n else 0.0
    avg_turn = total_duration_s / total_turns if total_turns else 0.0
    lines.append("")
    lines.append(
        f"_{n} scenario(s) executed — {findings_count} with finding(s). "
        f"Total: {total_duration_s:.1f}s | Avg per scenario: {avg_scenario:.1f}s | "
        f"Avg per turn: {avg_turn:.1f}s_"
    )
    lines.append("")
    return lines


def _render_artefact(lines: list[str], art: Any) -> None:  # noqa: ANN401
    """Render one RemediationArtefact as Markdown bullets."""
    from nuguard.behavior.models import RemediationArtefactType

    atype = art.artefact_type
    priority_badge = f"[{art.priority.upper()}]"
    finding_ref = f" *(findings: {', '.join(art.finding_ids)})*" if art.finding_ids else ""

    if atype == RemediationArtefactType.SYSTEM_PROMPT_PATCH:
        loc = f" — `{art.patch_location}`" if art.patch_location else ""
        lines.append(f"**{priority_badge} System Prompt Patch — {art.patch_section or 'Security Rules'}{loc}**{finding_ref}")
        lines.append("")
        if art.patch_text:
            lines.append("```")
            lines.append(art.patch_text.strip())
            lines.append("```")
        privilege_notes = _privilege_notes(art)
        if privilege_notes:
            lines.append(privilege_notes)
        lines.append(f"*Rationale*: {art.rationale}")
        lines.append("")

    elif atype in (
        RemediationArtefactType.INPUT_GUARDRAIL,
        RemediationArtefactType.OUTPUT_GUARDRAIL,
    ):
        label = "Input Guardrail" if atype == RemediationArtefactType.INPUT_GUARDRAIL else "Output Guardrail"
        lines.append(f"**{priority_badge} {label} — `{art.guardrail_name or 'unnamed'}`**{finding_ref}")
        lines.append("")
        lines.append(f"- **Type**: `{art.guardrail_type or 'unspecified'}`")
        if art.guardrail_trigger:
            lines.append(f"- **Trigger**: `{art.guardrail_trigger}`")
        if art.guardrail_action:
            lines.append(f"- **Action**: `{art.guardrail_action}`")
        if art.guardrail_message:
            lines.append(f"- **Message**: _{art.guardrail_message}_")
        privilege_notes = _privilege_notes(art)
        if privilege_notes:
            lines.append(privilege_notes)
        lines.append(f"- **Rationale**: {art.rationale}")
        lines.append("")

    elif atype == RemediationArtefactType.ARCHITECTURAL_CHANGE:
        lines.append(f"**{priority_badge} Architectural Change — {art.change_description or 'see details'}**{finding_ref}")
        lines.append("")
        if art.change_detail:
            for detail_line in art.change_detail.splitlines():
                lines.append(detail_line)
        if art.edge_to_remove:
            lines.append(f"- **Remove CALLS edge**: `{art.edge_to_remove[0]}` → `{art.edge_to_remove[1]}`")
        privilege_notes = _privilege_notes(art)
        if privilege_notes:
            lines.append(privilege_notes)
        lines.append("")
        lines.append(f"*Rationale*: {art.rationale}")
        lines.append("")

    else:
        lines.append(f"**{priority_badge} {art.artefact_type.value}** — {art.rationale}{finding_ref}")
        lines.append("")


def _privilege_notes(art: Any) -> str:
    """Return a privilege-context note line or empty string."""
    parts: list[str] = []
    if art.privilege_scope:
        parts.append(f"privilege: `{art.privilege_scope}`")
    if art.requires_auth:
        parts.append("requires authentication")
    if art.requires_hitl:
        parts.append("requires HITL approval")
    return ("- **Access controls**: " + ", ".join(parts)) if parts else ""


def to_text(result: "BehaviorAnalysisResult", meta: "ReportMeta | None" = None) -> None:
    """Print Rich-formatted text report to console.

    Args:
        result: Complete BehaviorAnalysisResult.
        meta: Optional report metadata.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    # Summary panel
    summary_lines = [
        f"Intent:        {result.intent.app_purpose or 'not determined'}",
        f"Risk Score:    {result.overall_risk_score:.1f} / 10",
        f"Coverage:      {result.coverage_percentage * 100:.0f}%",
        f"Alignment:     {result.intent_alignment_score:.2f} / 5.0",
        f"Findings:      {len(result.static_findings) + len(result.dynamic_findings)}",
        f"Outcome:       {result.scan_outcome}",
    ]
    console.print(Panel("\n".join(summary_lines), title="Behavior Analysis Summary", border_style="blue"))

    # Coverage table
    if result.coverage:
        table = Table(title="Component Coverage")
        table.add_column("Component")
        table.add_column("Type")
        table.add_column("Exercised")
        table.add_column("Within Policy")
        table.add_column("Deviations")
        for cov in result.coverage:
            ex = "[green]Yes[/green]" if cov.exercised else "[red]No[/red]"
            wp = "[green]Yes[/green]" if cov.exercised_within_policy else ("[red]No[/red]" if cov.exercised else "-")
            console.print_row = table.add_row  # type: ignore[attr-defined]
            table.add_row(cov.component_name, cov.node_type, ex, wp, str(len(cov.deviations)))
        console.print(table)

    # Findings
    all_findings = list(result.static_findings) + list(result.dynamic_findings)
    if all_findings:
        console.print("\n[bold]Findings[/bold]")
        for f in all_findings:
            sev = str(f.get("severity", "")).upper()
            color = {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "blue"}.get(sev, "white")
            console.print(f"  [{color}][{sev}][/{color}] {f.get('title', '')}")
