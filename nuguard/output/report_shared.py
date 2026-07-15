"""Shared report-generation utilities for behavior and redteam Markdown reports.

Both :mod:`nuguard.behavior.report` and :mod:`nuguard.redteam.report` import
from here to keep formatting consistent and avoid duplication.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


def _norm_sev(raw: object) -> str:
    """Normalize a severity value to a plain uppercase string (e.g. HIGH, CRITICAL).

    Handles plain strings (``'high'``) and enum-style strings (``'Severity.HIGH'``).
    """
    s = str(raw).upper()
    return s.split(".")[-1] if "." in s else s


def _truncate_evidence(text: str, *, limit: int = 2500) -> str:
    """Trim *text* to *limit* chars at a newline/word boundary."""
    return safe_truncate(text, limit=limit, suffix="… (truncated)")


def safe_truncate(text: str, *, limit: int = 2500, suffix: str = "…") -> str:
    """Trim text safely at a word boundary and append suffix."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    window = max(200, limit // 5)
    last_nl = cut.rfind("\n", limit - window)
    if last_nl != -1:
        return cut[:last_nl] + "\n" + suffix
    last_sp = cut.rfind(" ", limit - 80)
    if last_sp != -1:
        return cut[:last_sp] + " " + suffix
    return cut + suffix


def sanitize_markdown_block(text: str) -> str:
    """Sanitize text before embedding in markdown sections."""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", cleaned)
    return cleaned


def render_finding_block(
    lines: list[str],
    finding: Any,
    *,
    heading_level: str = "##",
) -> None:
    """Render a single finding in the redteam-style Markdown format.

    Accepts either a dict (behavior dynamic findings) or a Finding Pydantic
    object (redteam findings).  Redteam-specific fields (attack steps, progress
    score, success indicator) are NOT rendered here — callers append those after.

    Args:
        lines: Accumulator list to append Markdown lines into.
        finding: Dict or Finding object representing one finding.
        heading_level: Markdown heading prefix, e.g. ``"##"`` or ``"###"``.
    """
    if isinstance(finding, dict):
        title = finding.get("title", "")
        finding_id = finding.get("finding_id", "")
        sev = _norm_sev(finding.get("severity", ""))
        comp = finding.get("affected_component", "")
        desc = finding.get("description", "")
        reasoning = finding.get("reasoning") or ""
        evidence_quote = finding.get("evidence_quote") or finding.get("evidence") or ""
        remediation = finding.get("remediation") or ""
        owasp_llm = finding.get("owasp_llm_ref") or ""
        owasp_asi = finding.get("owasp_asi_ref") or ""
        policy_clause = finding.get("policy_clause") or ""
        goal_type = finding.get("goal_type") or ""
    else:
        title = finding.title or ""
        finding_id = getattr(finding, "finding_id", "") or ""
        sev = (
            finding.severity.value.upper()
            if hasattr(finding.severity, "value")
            else str(finding.severity).upper()
        )
        comp = getattr(finding, "affected_component", "") or ""
        desc = getattr(finding, "description", "") or ""
        reasoning = getattr(finding, "reasoning", "") or ""
        evidence_quote = getattr(finding, "evidence_quote", "") or ""
        remediation = getattr(finding, "remediation", "") or ""
        owasp_llm = getattr(finding, "owasp_llm_ref", "") or ""
        owasp_asi = ""
        policy_clause = ""
        goal_type = str(finding.goal_type) if getattr(finding, "goal_type", None) else ""

    heading = f"{heading_level} [{sev}] {title}"
    if finding_id:
        heading += f" — {finding_id}"
    lines.append(heading)
    lines.append("")
    if desc and desc != title:
        lines.append(desc)
        lines.append("")
    if comp:
        lines.append(f"**Affected Component:** {comp}")
        lines.append("")
    if goal_type:
        lines.append(f"**Type:** {goal_type}")
        lines.append("")
    if policy_clause:
        lines.append(f"**Policy Clause:** {policy_clause}")
        lines.append("")
    if reasoning:
        lines.append(f"**Finding:** {reasoning}")
        lines.append("")
    if evidence_quote:
        lines.append("**Evidence:**")
        evidence = sanitize_markdown_block(_truncate_evidence(evidence_quote, limit=1000))
        lines.append("> " + evidence.replace("\n", "\n> "))
        lines.append("")
    if remediation:
        lines.append(f"**Remediation:** {remediation}")
        lines.append("")
    if owasp_llm:
        lines.append(f"**OWASP LLM:** {owasp_llm}")
        lines.append("")
    if owasp_asi:
        lines.append(f"**OWASP ASI:** {owasp_asi}")
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


def _render_artefact(lines: list[str], art: Any) -> None:
    """Render one RemediationArtefact as Markdown bullets."""
    from nuguard.remediation.models import RemediationArtefactType

    atype = art.artefact_type
    priority_badge = f"[{art.priority.upper()}]"
    finding_ref = f" *(findings: {', '.join(art.finding_ids)})*" if art.finding_ids else ""

    if atype == RemediationArtefactType.SYSTEM_PROMPT_PATCH:
        loc = f" — `{art.patch_location}`" if art.patch_location else ""
        lines.append(
            f"**{priority_badge} System Prompt Patch — {art.patch_section or 'Security Rules'}{loc}**{finding_ref}"
        )
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
        label = (
            "Input Guardrail"
            if atype == RemediationArtefactType.INPUT_GUARDRAIL
            else "Output Guardrail"
        )
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
        lines.append(
            f"**{priority_badge} Architectural Change — {art.change_description or 'see details'}**{finding_ref}"
        )
        lines.append("")
        if art.change_detail:
            for detail_line in art.change_detail.splitlines():
                lines.append(detail_line)
        if art.edge_to_remove:
            lines.append(
                f"- **Remove CALLS edge**: `{art.edge_to_remove[0]}` → `{art.edge_to_remove[1]}`"
            )
        privilege_notes = _privilege_notes(art)
        if privilege_notes:
            lines.append(privilege_notes)
        lines.append("")
        lines.append(f"*Rationale*: {art.rationale}")
        lines.append("")

    else:
        lines.append(
            f"**{priority_badge} {art.artefact_type.value}** — {art.rationale}{finding_ref}"
        )
        lines.append("")


def render_remediation_plan_section(lines: list[str], remediation_plan: list) -> None:
    """Append a ``## Remediation Plan`` section to *lines*, grouped by SBOM node."""
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
