"""RecommendationEngine — generate remediation recommendations from findings and deviations."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nuguard.behavior.models import Recommendation

if TYPE_CHECKING:
    from nuguard.behavior.models import BehaviorAnalysisResult

_log = logging.getLogger(__name__)


class RecommendationEngine:
    """Generate deterministic remediation recommendations from findings and deviations."""

    def generate(self, result: "BehaviorAnalysisResult") -> list[Recommendation]:
        """Generate recommendations from static and dynamic findings.

        Args:
            result: Complete BehaviorAnalysisResult.

        Returns:
            Sorted list of Recommendation objects (critical first).
        """
        recs: list[Recommendation] = []
        all_findings = list(result.static_findings) + list(result.dynamic_findings)

        for finding in all_findings:
            rec = self._finding_to_recommendation(finding)
            if rec is not None:
                recs.append(rec)

        # Coverage-based recommendations
        for cov in result.coverage:
            if not cov.exercised:
                recs.append(
                    Recommendation(
                        component=cov.component_name,
                        recommendation_type="tool_config",
                        description=f"Verify {cov.component_name} is correctly wired and accessible",
                        rationale=f"{cov.component_name} was never exercised during behavior testing",
                        priority="low",
                        related_findings=[],
                    )
                )
            elif cov.deviations:
                high_sev = any(d.severity in ("critical", "high") for d in cov.deviations)
                recs.append(
                    Recommendation(
                        component=cov.component_name,
                        recommendation_type="system_prompt",
                        description=f"Review and fix behavioral deviations for {cov.component_name}",
                        rationale=f"{cov.component_name} showed {len(cov.deviations)} deviation(s) during testing",
                        priority="high" if high_sev else "medium",
                        related_findings=[],
                    )
                )

        # Deduplicate and sort
        seen: set[str] = set()
        unique_recs: list[Recommendation] = []
        for r in recs:
            key = f"{r.component}:{r.recommendation_type}:{r.description[:60]}"
            if key not in seen:
                seen.add(key)
                unique_recs.append(r)

        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        unique_recs.sort(key=lambda r: priority_order.get(r.priority, 99))
        return unique_recs

    def _finding_to_recommendation(self, finding: dict) -> Recommendation | None:
        """Convert a single finding dict to a Recommendation."""
        finding_id = str(finding.get("finding_id", ""))
        title = str(finding.get("title", ""))
        severity = str(finding.get("severity", "medium")).lower()
        component = str(finding.get("affected_component", "unknown"))
        description = str(finding.get("description", ""))

        # BA-001: system prompt restricted topic
        if "BA-001" in finding_id or "restricted topic" in title.lower():
            topic = _extract_quoted(title) or "restricted topic"
            return Recommendation(
                component=component,
                recommendation_type="system_prompt",
                description=f"Remove references to '{topic}' from {component}'s system prompt",
                rationale=description,
                priority=_severity_to_priority(severity),
                related_findings=[finding_id],
            )

        # BA-002: risky tool no guardrail
        if "BA-002" in finding_id or "no guardrail" in title.lower():
            return Recommendation(
                component=component,
                recommendation_type="guardrail",
                description=f"Add input validation guardrail before {component}",
                rationale=description,
                priority=_severity_to_priority(severity),
                related_findings=[finding_id],
            )

        # BA-005: no auth high privilege
        if "BA-005" in finding_id or "unauthenticated" in title.lower():
            return Recommendation(
                component=component,
                recommendation_type="architecture",
                description=f"Add authentication requirement to {component}",
                rationale=description,
                priority="critical",
                related_findings=[finding_id],
            )

        # Policy violation
        if "POLICY_VIOLATION" in title.upper() or "policy violation" in title.lower():
            clause = str(finding.get("policy_clause", ""))
            return Recommendation(
                component=component,
                recommendation_type="system_prompt",
                description=f"Add violated clause to blocked_topics/actions: {clause[:80]}",
                rationale=description,
                priority=_severity_to_priority(severity),
                related_findings=[finding_id],
            )

        # Capability gap
        if "CAPABILITY_GAP" in title.upper() or "capability gap" in title.lower():
            return Recommendation(
                component=component,
                recommendation_type="tool_config",
                description=f"Verify {component} is correctly wired and returns expected output",
                rationale=description,
                priority=_severity_to_priority(severity),
                related_findings=[finding_id],
            )

        # Intent misalignment
        if "INTENT_MISALIGNMENT" in title.upper() or "intent misalignment" in title.lower():
            return Recommendation(
                component=component,
                recommendation_type="system_prompt",
                description=f"Align {component} system prompt with application's stated purpose",
                rationale=description,
                priority=_severity_to_priority(severity),
                related_findings=[finding_id],
            )

        # Canary / secret disclosure
        if "canary" in title.lower() or "secret" in title.lower() or "disclosure" in title.lower():
            return Recommendation(
                component=component,
                recommendation_type="guardrail",
                description=f"Add output sanitization to prevent canary/secret disclosure via {component}",
                rationale=description,
                priority="critical",
                related_findings=[finding_id],
            )

        # Generic finding → low priority recommendation
        if description:
            return Recommendation(
                component=component,
                recommendation_type="system_prompt",
                description=f"Review and remediate: {title[:80]}",
                rationale=description,
                priority=_severity_to_priority(severity),
                related_findings=[finding_id],
            )

        return None


def _severity_to_priority(severity: str) -> str:
    mapping = {"critical": "critical", "high": "high", "medium": "medium", "low": "low", "info": "low"}
    return mapping.get(severity.lower(), "medium")


def _extract_quoted(text: str) -> str | None:
    """Extract the first single-quoted string from text."""
    import re
    m = re.search(r"'([^']+)'", text)
    return m.group(1) if m else None
