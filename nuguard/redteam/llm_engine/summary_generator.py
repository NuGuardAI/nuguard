"""LLM-powered executive summary and coding-agent brief generator."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nuguard.common.llm_client import LLMClient
from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding

if TYPE_CHECKING:
    pass

_log = get_logger(__name__)

_EXEC_SUMMARY_SYSTEM = (
    "You are a security engineer summarising an AI red-team scan report. "
    "Write concise, technical prose. Do NOT use bullet lists or headers."
)

_BEHAVIOR_EXEC_SUMMARY_SYSTEM = (
    "You are a security engineer summarising an AI behavior analysis report. "
    "Write concise, technical prose. Do NOT use bullet lists or headers."
)

_CODING_BRIEF_SYSTEM = (
    "You are a lead security engineer producing a remediation task list for a coding agent. "
    "The agent has access to the source code but needs precise, unambiguous instructions."
)


def _sev_counts(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[str(f.severity)] = counts.get(str(f.severity), 0) + 1
    return counts


class LLMSummaryGenerator:
    """Generates executive summaries and the coding-agent brief using the eval LLM.

    Per-finding remediation text is generated elsewhere — by
    :class:`nuguard.remediation.synthesizer.RemediationSynthesizer` — and
    backfilled onto ``Finding.remediation`` via
    :func:`nuguard.remediation.backfill.backfill_finding_remediation` before
    :meth:`coding_agent_brief` is called.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def executive_summary(
        self,
        target_url: str,
        scenarios_run: int,
        findings: list[Finding],
        frameworks: list[str],
        duration_s: float,
    ) -> str:
        """Return a 2–4 sentence executive summary string."""
        counts = _sev_counts(findings)
        critical = counts.get("critical", 0)
        high = counts.get("high", 0)
        medium = counts.get("medium", 0)
        low = counts.get("low", 0)

        finding_lines = []
        for f in findings[:10]:
            finding_lines.append(
                f"- [{f.severity}] {f.title}: {f.affected_component or 'unknown component'} — "
                f"{(f.evidence or f.description or '')[:200]}"
            )

        prompt = (
            f"Scan statistics:\n"
            f"- Target: {target_url}\n"
            f"- Scenarios run: {scenarios_run}\n"
            f"- Findings: {len(findings)} "
            f"({critical} critical, {high} high, {medium} medium, {low} low)\n"
            f"- Frameworks detected: {', '.join(frameworks) or 'unknown'}\n"
            f"- Scan duration: {duration_s:.0f}s\n"
            f"\nFindings:\n" + "\n".join(finding_lines) + "\n\n"
            "Write a 2–4 sentence executive summary for a technical audience. "
            "Focus on: what was found, what the risk is, and the urgency of remediation. "
            "Do NOT repeat finding titles verbatim — synthesise."
        )
        _log.debug(
            "summary-gen | executive-summary: %d findings, %d scenarios",
            len(findings), scenarios_run,
        )
        try:
            result = await self._llm.complete(
                prompt, system=_EXEC_SUMMARY_SYSTEM,
                label=f"summary-gen | executive-summary findings={len(findings)}",
            )
            if result.startswith("[NUGUARD_CANNED_RESPONSE]"):
                return ""
            return result.strip()
        except Exception as exc:
            _log.warning("Executive summary generation failed: %s", exc)
            return ""

    async def behavior_executive_summary(
        self,
        target_url: str,
        app_purpose: str,
        risk_score: float,
        coverage_pct: float,
        alignment_score: float,
        scenarios_run: int,
        static_findings: list[dict],
        dynamic_findings: list[dict],
        frameworks: list[str],
    ) -> str:
        """Return a 2–4 sentence executive summary for a behavior analysis report."""
        all_findings = list(static_findings) + list(dynamic_findings)
        counts: dict[str, int] = {}
        for f in all_findings:
            sev = str(f.get("severity", "unknown")).lower().rsplit(".", 1)[-1]
            counts[sev] = counts.get(sev, 0) + 1

        finding_lines = []
        for f in all_findings[:10]:
            sev = str(f.get("severity", "unknown")).lower().rsplit(".", 1)[-1]
            title = f.get("title", "unknown")
            comp = f.get("affected_component", "unknown component")
            desc = (f.get("description") or "")[:200]
            finding_lines.append(f"- [{sev}] {title}: {comp} — {desc}")

        gap_types: list[str] = []
        for ftype in ("CAPABILITY_GAP", "INTENT_MISALIGNMENT", "TOOL_CHAIN_BROKEN"):
            if any(f.get("finding_type") == ftype for f in dynamic_findings):
                gap_types.append(ftype.replace("_", " ").title())

        prompt = (
            f"Behavior analysis statistics:\n"
            f"- Target: {target_url}\n"
            f"- App purpose: {app_purpose or 'unknown'}\n"
            f"- Frameworks detected: {', '.join(frameworks) or 'unknown'}\n"
            f"- Scenarios run: {scenarios_run}\n"
            f"- Overall risk score: {risk_score:.1f} / 100\n"
            f"- Component coverage: {coverage_pct * 100:.0f}%\n"
            f"- Intent alignment score: {alignment_score:.2f} / 5.0\n"
            f"- Findings: {len(all_findings)} "
            f"({counts.get('critical', 0)} critical, {counts.get('high', 0)} high, "
            f"{counts.get('medium', 0)} medium, {counts.get('low', 0)} low)\n"
        )
        if gap_types:
            prompt += f"- Behavioral gaps: {', '.join(gap_types)}\n"
        if finding_lines:
            prompt += "\nFindings:\n" + "\n".join(finding_lines) + "\n"
        prompt += (
            "\nWrite a 2–4 sentence executive summary for a technical audience. "
            "Focus on: what the AI application does, what behavioral issues were found, "
            "what the risk is, and the urgency of remediation. "
            "Do NOT repeat finding titles verbatim — synthesise."
        )
        _log.debug(
            "summary-gen | behavior-executive-summary: %d findings, %d scenarios",
            len(all_findings), scenarios_run,
        )
        try:
            result = await self._llm.complete(
                prompt, system=_BEHAVIOR_EXEC_SUMMARY_SYSTEM,
                label=f"summary-gen | behavior-executive-summary findings={len(all_findings)}",
            )
            if result.startswith("[NUGUARD_CANNED_RESPONSE]"):
                return ""
            return result.strip()
        except Exception as exc:
            _log.warning("Behavior executive summary generation failed: %s", exc)
            return ""

    async def coding_agent_brief(
        self,
        findings: list[Finding],
    ) -> str:
        """Return the full coding-agent brief as a Markdown string.

        Sources each finding's remediation text from ``finding.remediation``
        — callers should run
        :func:`nuguard.remediation.backfill.backfill_finding_remediation`
        before invoking this so that text reflects the LLM-synthesized
        remediation plan rather than being blank.
        """
        if not findings:
            return ""

        findings_text = []
        for f in findings:
            rem = f.remediation or ""
            findings_text.append(
                f"**[{f.severity}] {f.title}**\n"
                f"Component: {f.affected_component or 'unknown'}\n"
                f"Remediation: {rem[:400]}"
            )

        prompt = (
            "Below are the findings from an AI red-team scan:\n\n"
            + "\n\n".join(findings_text)
            + "\n\nProduce a numbered list of remediation tasks. Each task must:\n"
            "1. State the file to edit (only if a single source file is implicated; "
            "otherwise name the component).\n"
            "2. Describe the exact code change in one or two sentences.\n"
            "3. Reference the relevant OWASP control.\n\n"
            "Do not include explanatory prose between tasks. Format:\n\n"
            "## Remediation Tasks\n\n"
            "1. **[{severity}] {component}** — {precise action}.  ({OWASP ref})\n"
            "2. ..."
        )
        _log.debug("summary-gen | coding-agent-brief: %d findings", len(findings))
        try:
            result = await self._llm.complete(
                prompt, system=_CODING_BRIEF_SYSTEM,
                label=f"summary-gen | coding-agent-brief findings={len(findings)}",
            )
            if result.startswith("[NUGUARD_CANNED_RESPONSE]"):
                return ""
            return result.strip()
        except Exception as exc:
            _log.warning("Coding agent brief generation failed: %s", exc)
            return ""
