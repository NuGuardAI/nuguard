"""LLM-powered executive summary and remediation brief generator."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from nuguard.common.llm_client import LLMClient
from nuguard.models.finding import Finding

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

_EXEC_SUMMARY_SYSTEM = (
    "You are a security engineer summarising an AI red-team scan report. "
    "Write concise, technical prose. Do NOT use bullet lists or headers."
)

_REMEDIATION_SYSTEM = (
    "You are a security engineer writing remediation steps for a developer. "
    "Use imperative sentences. Be concrete. Max 5 steps."
)

_REMEDIATION_BATCH_SYSTEM = (
    "You are a security engineer writing remediation steps for a developer. "
    "You will receive multiple findings grouped by component and attack goal. "
    "For each finding, write concise, actionable steps. "
    "Use imperative sentences. Be concrete. Max 5 steps per finding."
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
    """Generates executive summary and per-finding remediations using the eval LLM."""

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

    async def remediation(
        self,
        finding: Finding,
        sbom_nodes: dict[str, object],
    ) -> str:
        """
        Return LLM-generated remediation for a single finding.
        Includes filepath only when finding.sbom_path has exactly one node
        with a known source_file metadata field.
        """
        # Resolve SBOM node details
        sbom_path_descriptions = finding.sbom_path_descriptions or []
        source_file: str | None = None

        if len(finding.sbom_path) == 1:
            node = sbom_nodes.get(finding.sbom_path[0])
            if node is not None:
                meta = getattr(node, "metadata", None)
                if meta:
                    source_file = getattr(meta, "source_file", None)

        # Build attack steps summary
        step_lines = []
        for i, step in enumerate(finding.attack_steps[:5], 1):
            step_type = step.get("step_type", "?")
            ok = "succeeded" if step.get("succeeded") else "failed"
            resp = (step.get("response") or "")[:150].replace("\n", " ")
            step_lines.append(f"  Step {i} ({step_type}, {ok}): {resp}")

        prompt_lines = [
            f"Finding: {finding.title}",
            f"Severity: {finding.severity}",
            f"Attack goal: {finding.goal_type or 'unknown'}",
            f"Affected component: {finding.affected_component or 'unknown'}",
            f"Evidence: {(finding.evidence or finding.description or '')[:300]}",
        ]
        if sbom_path_descriptions:
            prompt_lines.append(f"SBOM path: {' \u2192 '.join(sbom_path_descriptions)}")
        if source_file:
            prompt_lines.append(f"Source file: {source_file}")
        if step_lines:
            prompt_lines.append("Attack steps that succeeded:")
            prompt_lines.extend(step_lines)

        prompt_lines.append("")
        if source_file:
            prompt_lines.append(
                "Write a specific, actionable remediation. Reference the source file path."
            )
        else:
            prompt_lines.append(
                "Write a specific, actionable remediation. "
                "Do NOT reference specific filenames — this component appears in multiple locations."
            )
        prompt_lines += [
            "Rules:",
            '- Use imperative sentences ("Add a guard\u2026", "Replace X with Y\u2026")',
            "- Be concrete enough that a coding agent can implement without asking questions",
            "- Max 5 steps; shorter is better",
            "- Do not restate what the attack did — only what to fix",
        ]

        _log.debug(
            "summary-gen | remediation: finding=%r severity=%s component=%s",
            finding.title, finding.severity, finding.affected_component,
        )
        try:
            result = await self._llm.complete(
                "\n".join(prompt_lines), system=_REMEDIATION_SYSTEM,
                label=f"summary-gen | remediation finding={finding.finding_id!r}",
            )
            if result.startswith("[NUGUARD_CANNED_RESPONSE]"):
                return ""
            return result.strip()
        except Exception as exc:
            _log.warning("Remediation generation failed for %s: %s", finding.finding_id, exc)
            return ""

    async def remediation_batch(
        self,
        findings: list[Finding],
        sbom_nodes: dict[str, object],
    ) -> dict[str, str]:
        """Return ``{finding_id: remediation_text}`` for all findings.

        Groups findings by ``(affected_component, goal_type)`` and issues one
        LLM call per cluster.  Falls back to per-finding calls for any cluster
        whose bulk response cannot be parsed.
        """
        if not findings:
            return {}

        # Group by (affected_component, goal_type)
        clusters: dict[tuple[str, str], list[Finding]] = {}
        for f in findings:
            key = (f.affected_component or "unknown", f.goal_type or "unknown")
            clusters.setdefault(key, []).append(f)

        _log.info(
            "summary-gen | remediation_batch: %d findings across %d clusters",
            len(findings), len(clusters),
        )

        result: dict[str, str] = {}
        for (component, goal_type), cluster_findings in clusters.items():
            cluster_result = await self._remediation_cluster(
                cluster_findings, component, goal_type, sbom_nodes
            )
            result.update(cluster_result)
        return result

    async def _remediation_cluster(
        self,
        findings: list[Finding],
        component: str,
        goal_type: str,
        sbom_nodes: dict[str, object],
    ) -> dict[str, str]:
        """One LLM call for all findings in a (component, goal_type) cluster.

        Parses per-finding sections from the response. Falls back to individual
        `remediation()` calls if parsing fails or the bulk call errors.
        """
        if len(findings) == 1:
            rem = await self.remediation(findings[0], sbom_nodes)
            return {findings[0].finding_id: rem} if rem else {}

        # Build combined prompt
        finding_blocks = []
        for i, f in enumerate(findings, 1):
            source_file: str | None = None
            if len(f.sbom_path) == 1:
                node = sbom_nodes.get(f.sbom_path[0])
                if node is not None:
                    meta = getattr(node, "metadata", None)
                    if meta:
                        source_file = getattr(meta, "source_file", None)
            evidence_text = (f.evidence or f.description or "")[:200]
            block = [
                f"## FINDING {i}: {f.title}",
                f"ID: {f.finding_id}",
                f"Severity: {f.severity}",
                f"Evidence: {evidence_text}",
            ]
            if source_file:
                block.append(f"Source file: {source_file}")
            finding_blocks.append("\n".join(block))

        prompt = (
            f"Component: {component}\n"
            f"Attack goal: {goal_type}\n\n"
            + "\n\n".join(finding_blocks)
            + "\n\n"
            "For each finding above, write 3–5 concrete remediation steps. "
            "Format output as:\n\n"
            "## FINDING 1\n<steps>\n\n## FINDING 2\n<steps>\n\n..."
            "\n\nDo NOT include the finding title or ID in your output — just the steps."
        )

        label = f"summary-gen | remediation-cluster component={component!r} n={len(findings)}"
        try:
            raw = await self._llm.complete(
                prompt, system=_REMEDIATION_BATCH_SYSTEM, label=label,
            )
            if not raw.startswith("[NUGUARD_CANNED_RESPONSE]"):
                # Parse sections by "## FINDING N"
                parts = re.split(r"(?m)^## FINDING\s+\d+\s*\n?", raw)
                # parts[0] is preamble (empty), then one section per finding
                finding_sections = [p.strip() for p in parts if p.strip()]
                if len(finding_sections) >= len(findings):
                    result: dict[str, str] = {}
                    for f, section in zip(findings, finding_sections):
                        if section:
                            result[f.finding_id] = section
                    if result:
                        _log.debug(
                            "remediation-cluster: parsed %d/%d findings for %r/%r",
                            len(result), len(findings), component, goal_type,
                        )
                        return result
                _log.info(
                    "remediation-cluster: parse mismatch (got %d sections for %d findings) "
                    "— falling back for %r/%r",
                    len(finding_sections), len(findings), component, goal_type,
                )
        except Exception as exc:
            _log.warning("remediation-cluster failed for %r/%r: %s", component, goal_type, exc)

        # Fallback: per-finding calls
        result = {}
        for f in findings:
            rem = await self.remediation(f, sbom_nodes)
            if rem:
                result[f.finding_id] = rem
        return result

    async def coding_agent_brief(
        self,
        findings: list[Finding],
        remediations: dict[str, str],
    ) -> str:
        """Return the full coding-agent brief as a Markdown string."""
        if not findings:
            return ""

        findings_text = []
        for f in findings:
            rem = remediations.get(f.finding_id, f.remediation or "")
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
            "1. **[{severity}] {component}** \u2014 {precise action}.  ({OWASP ref})\n"
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
