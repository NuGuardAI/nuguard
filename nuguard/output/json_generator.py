"""JSON output generators for redteam findings and remediation plans."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.models.finding import Finding


def write_remediation_plan(
    findings: list["Finding"],
    remediations: dict[str, str],
    output_path: Path,
    *,
    target_url: str = "",
    scan_id: str = "",
) -> None:
    """Write a machine-readable remediation plan to *output_path* as JSON.

    The plan is structured for consumption by coding agents, CI pipelines, and
    dashboards. Each finding entry includes the full finding context plus the
    LLM-generated remediation text (when available).

    Parameters
    ----------
    findings:
        The list of ``Finding`` objects from the redteam scan.
    remediations:
        ``{finding_id: remediation_text}`` from
        :meth:`~nuguard.redteam.llm_engine.summary_generator.LLMSummaryGenerator.remediation_batch`.
    output_path:
        Destination path. Parent directory must exist.
    target_url:
        The scanned target URL (for report metadata).
    scan_id:
        Optional scan identifier for correlation.
    """
    plan: dict = {
        "schema_version": "1.0",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "scan_id": scan_id or "",
        "target": target_url or "",
        "total_findings": len(findings),
        "findings": [
            _finding_to_dict(f, remediations.get(f.finding_id, ""))
            for f in findings
        ],
    }
    output_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def _finding_to_dict(f: "Finding", remediation_text: str) -> dict:
    """Convert a Finding to a remediation-plan dict entry."""
    scores = getattr(f, "scores", None) or {}
    return {
        "finding_id": f.finding_id,
        "title": f.title,
        "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
        "goal_type": f.goal_type or "",
        "affected_component": f.affected_component or "",
        "description": f.description or "",
        # Judge evidence fields
        "reasoning": getattr(f, "reasoning", "") or "",
        "evidence_quote": getattr(f, "evidence_quote", "") or "",
        "success_indicator": getattr(f, "success_indicator", None),
        "progress_score": scores.get("goal_progress"),
        # Remediation
        "remediation": remediation_text or f.remediation or "",
        # SBOM / compliance refs
        "sbom_path": f.sbom_path or [],
        "sbom_path_descriptions": f.sbom_path_descriptions or [],
        "owasp_asi_ref": f.owasp_asi_ref or "",
        "owasp_llm_ref": f.owasp_llm_ref or "",
        "mitre_atlas_technique": f.mitre_atlas_technique or "",
        "policy_clauses_violated": f.policy_clauses_violated or [],
        "chain_id": f.chain_id or "",
    }
