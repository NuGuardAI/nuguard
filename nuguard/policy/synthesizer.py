"""LLM evidence synthesizer for compliance control evaluation.

Ported from assessment_service/core/llm_synthesizer.py.  Adaptations:
  - Uses nuguard.common.llm_client.LLMClient instead of shared.llm_service.
  - Returns ControlEvaluation directly (no DB persistence).
  - The AIBOM snapshot dict replaces AibomEvidence / repo_hits.
  - Async throughout.
"""

from __future__ import annotations

import json
from typing import Any

from nuguard.common.llm_client import LLMClient
from nuguard.common.logging import get_logger
from nuguard.models.policy import (
    ComplianceControl,
    ComplianceResult,
    ControlEvaluation,
)
from nuguard.policy.scoring import safe_float

_log = get_logger(__name__)

# Maximum AIBOM nodes forwarded to the LLM prompt per control.
_MAX_NODES_IN_PROMPT = 10

# Synthesis status values
_STATUS_COVERED = "COVERED"
_STATUS_PARTIAL = "PARTIAL"
_STATUS_GAP = "GAP"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    return (
        "You are a compliance assessment engine specialising in AI application security. "
        "Analyse the provided AIBOM evidence to determine whether the application satisfies "
        "the given compliance control. "
        "Return strictly valid JSON with these fields: "
        "status (COVERED|PARTIAL|GAP), title (str), confidence (float 0-1), "
        "evidence_summary (str), evidence (list[str]), remediation (str). "
        "Status guidance: "
        "COVERED — all key criteria are directly and credibly evidenced. "
        "PARTIAL — some evidence exists but key criteria remain unmet. "
        "GAP — little or no relevant evidence found. "
        "Be conservative: prefer PARTIAL over COVERED when uncertain; prefer GAP over PARTIAL "
        "when evidence is weak. Always populate evidence_summary. "
        "Never use UUIDs as evidence references — cite file paths and names."
    )


def _build_user_prompt(
    control: ComplianceControl,
    snapshot: dict[str, Any],
) -> str:
    """Construct the user-facing prompt for LLM synthesis."""
    # Gather relevant nodes (those whose type appears in ai_sbom_basis)
    basis_types = {t.lower() for t in (control.ai_sbom_basis or [])}
    node_sections = snapshot.get("nodes", {})

    relevant_nodes: list[dict[str, Any]] = []
    for bucket_key, nodes in node_sections.items():
        if not basis_types or bucket_key.rstrip("s") in basis_types or bucket_key in basis_types:
            relevant_nodes.extend(nodes)
    # De-dup by id and cap
    seen_ids: set[str] = set()
    capped: list[dict[str, Any]] = []
    for n in relevant_nodes:
        nid = str(n.get("id", ""))
        if nid not in seen_ids:
            seen_ids.add(nid)
            capped.append(n)
        if len(capped) >= _MAX_NODES_IN_PROMPT:
            break

    signals = snapshot.get("signals", {})
    counts = snapshot.get("counts", {})

    lines = [
        "## Control under assessment",
        f"Control ID: {control.id}",
        f"Name: {control.name}",
        f"Description: {control.description}",
        f"Severity: {control.severity}",
        f"Framework: {control.framework}",
    ]
    if control.gap_diagnosis:
        lines.append(f"Gap diagnosis hint: {control.gap_diagnosis}")

    lines += [
        "",
        "## AIBOM Evidence",
        f"Signals: {json.dumps(signals, default=str)}",
        f"Counts: {json.dumps(counts, default=str)}",
        f"Relevant nodes ({len(capped)} shown):",
        json.dumps(capped, indent=2, default=str),
        "",
        "## Task",
        "Assess whether this control is COVERED, PARTIAL, or a GAP.",
        "Return JSON: {status, title, confidence, evidence_summary, evidence, remediation}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------


def map_to_compliance_result(
    llm_output: dict[str, Any],
) -> ComplianceResult:
    """Map LLM GAP/PARTIAL/COVERED output to ComplianceResult.

    COVERED                → PASS
    PARTIAL                → PARTIAL
    GAP, confidence >= 0.2 → FAIL
    GAP, confidence < 0.2  → UNABLE_TO_ASSESS
    """
    status = str(llm_output.get("status") or "GAP").upper()
    confidence = safe_float(llm_output.get("confidence"), 0.5)

    if status == _STATUS_COVERED:
        return ComplianceResult.PASS
    if status == _STATUS_PARTIAL:
        return ComplianceResult.PARTIAL
    # GAP
    if confidence < 0.2:
        return ComplianceResult.UNABLE_TO_ASSESS
    return ComplianceResult.FAIL


def _score_from_result(result: ComplianceResult) -> float:
    """Map a ComplianceResult to a representative numeric score."""
    mapping = {
        ComplianceResult.PASS: 1.0,
        ComplianceResult.PARTIAL: 0.5,
        ComplianceResult.FAIL: 0.0,
        ComplianceResult.UNABLE_TO_ASSESS: 0.0,
        ComplianceResult.NOT_APPLICABLE: 1.0,
    }
    return mapping.get(result, 0.0)


# ---------------------------------------------------------------------------
# Fallback response
# ---------------------------------------------------------------------------


def _fallback_response(control: ComplianceControl, exc: Exception) -> dict[str, Any]:
    return {
        "status": "GAP",
        "title": f"Unable to assess {control.id} — LLM unavailable",
        "confidence": 0.1,
        "evidence_summary": f"LLM synthesis failed: {exc}",
        "evidence": [],
        "remediation": "Retry the assessment when the LLM service is available.",
        "_fallback": True,
    }


# ---------------------------------------------------------------------------
# Main async entry point
# ---------------------------------------------------------------------------


async def llm_synthesize(
    control: ComplianceControl,
    snapshot: dict[str, Any],
    llm: LLMClient | None = None,
) -> ControlEvaluation:
    """Synthesise AIBOM evidence into a ControlEvaluation using an LLM.

    Args:
        control: The ComplianceControl being assessed.
        snapshot: AIBOM snapshot dict from build_aibom_snapshot().
        llm: LLMClient instance.  A default (canned-response) client is used
             when None is passed.

    Returns:
        A ControlEvaluation with the LLM-derived result.
    """
    if llm is None:
        llm = LLMClient()

    user_prompt = _build_user_prompt(control, snapshot)
    system_prompt = _build_system_prompt()

    raw_output: dict[str, Any]
    try:
        raw_text = await llm.complete(user_prompt, system=system_prompt)
        # Attempt to parse JSON from the response
        # Strip any markdown code fences
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw_output = json.loads(cleaned)
        if not isinstance(raw_output, dict) or "status" not in raw_output:
            raise ValueError(f"LLM returned unexpected shape: {type(raw_output)}")
    except Exception as exc:
        _log.warning(
            "llm_synthesizer.fallback control_id=%s error=%s",
            control.id,
            exc,
        )
        raw_output = _fallback_response(control, exc)

    result = map_to_compliance_result(raw_output)
    score = _score_from_result(result)
    evidence: list[str] = [
        (item if isinstance(item, str) else str(item))
        for item in (raw_output.get("evidence") or [])
    ]
    evidence_summary = str(raw_output.get("evidence_summary") or "")
    if evidence_summary and evidence_summary not in evidence:
        evidence = [evidence_summary] + evidence

    _log.info(
        "llm_synthesizer.completed control_id=%s status=%s confidence=%s",
        control.id,
        raw_output.get("status"),
        raw_output.get("confidence"),
    )

    return ControlEvaluation(
        control=control,
        result=result,
        score=score,
        evidence=evidence,
        gaps=[control.gap_diagnosis] if result in (ComplianceResult.FAIL, ComplianceResult.PARTIAL) else [],
        remediation=str(raw_output.get("remediation") or control.fix_guidance or ""),
    )
