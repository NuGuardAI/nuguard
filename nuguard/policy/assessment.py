"""Full compliance assessment pipeline.

Orchestrates: snapshot → load controls → evaluate → LLM fallback → aggregate.
"""

from __future__ import annotations

from nuguard.common.llm_client import LLMClient
from nuguard.common.logging import get_logger
from nuguard.models.policy import (
    ComplianceResult,
    ControlEvaluation,
    PolicyAssessmentResult,
)
from nuguard.policy.aibom_snapshot import build_aibom_snapshot
from nuguard.policy.compliance.loader import load_controls
from nuguard.policy.evaluator import evaluate_control_from_sbom
from nuguard.policy.synthesizer import llm_synthesize
from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

# Severity → weight multiplier used in the final weighted score.
_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 3.0,
    "high": 2.0,
    "medium": 1.0,
    "low": 1.0,
}


async def run_compliance_assessment(
    doc: AiSbomDocument,
    framework: str,
    enable_llm: bool = False,
    llm: LLMClient | None = None,
) -> PolicyAssessmentResult:
    """Run a full compliance assessment against *framework* for *doc*.

    Pipeline
    --------
    1. Build AIBOM snapshot from the document.
    2. Load controls for the framework.
    3. For each control:
       a. Run deterministic CCD evaluator.
       b. If result is UNABLE_TO_ASSESS and *enable_llm* is True, fall back
          to the LLM synthesizer.
    4. Compute per-status counts and weighted compliance score.
    5. Return PolicyAssessmentResult.

    Args:
        doc: AiSbomDocument to assess.
        framework: Framework name, e.g. ``"owasp-llm-top10"``.
        enable_llm: Whether to use the LLM synthesizer for controls that the
            deterministic evaluator cannot assess.
        llm: LLMClient to use when *enable_llm* is True.  A default client is
            created when None.

    Returns:
        PolicyAssessmentResult with evaluation details and aggregate scores.
    """
    _log.debug("run_compliance_assessment framework=%s llm=%s", framework, enable_llm)

    # Step 1: build snapshot
    snapshot = build_aibom_snapshot(doc)

    # Step 2: load controls
    controls = load_controls(framework=framework)
    if not controls:
        _log.warning("No controls loaded for framework %r", framework)
        return PolicyAssessmentResult(
            framework=framework,
            total_controls=0,
            score=0.0,
        )

    # Step 3: evaluate each control
    evaluations: list[ControlEvaluation] = []
    for control in controls:
        eval_result = evaluate_control_from_sbom(control, snapshot, doc)

        if (
            eval_result.result == ComplianceResult.UNABLE_TO_ASSESS
            and enable_llm
        ):
            _log.debug(
                "Falling back to LLM for control %s", control.id
            )
            try:
                eval_result = await llm_synthesize(control, snapshot, llm)
            except Exception as exc:
                _log.warning(
                    "LLM synthesis failed for control %s: %s", control.id, exc
                )

        evaluations.append(eval_result)

    # Step 4: aggregate
    pass_count = sum(1 for e in evaluations if e.result == ComplianceResult.PASS)
    partial_count = sum(1 for e in evaluations if e.result == ComplianceResult.PARTIAL)
    fail_count = sum(1 for e in evaluations if e.result == ComplianceResult.FAIL)
    unable_count = sum(
        1 for e in evaluations
        if e.result in (ComplianceResult.UNABLE_TO_ASSESS, ComplianceResult.NOT_APPLICABLE)
    )

    # Weighted score: skip UNABLE_TO_ASSESS and NOT_APPLICABLE from denominator
    total_weight = 0.0
    weighted_sum = 0.0
    for ev in evaluations:
        if ev.result in (ComplianceResult.UNABLE_TO_ASSESS, ComplianceResult.NOT_APPLICABLE):
            continue
        weight = _SEVERITY_WEIGHTS.get(ev.control.severity.lower(), 1.0)
        total_weight += weight
        weighted_sum += ev.score * weight

    score = (weighted_sum / total_weight) if total_weight > 0.0 else 0.0

    _log.info(
        "compliance_assessment_complete framework=%s controls=%d pass=%d partial=%d fail=%d unable=%d score=%.3f",
        framework,
        len(controls),
        pass_count,
        partial_count,
        fail_count,
        unable_count,
        score,
    )

    return PolicyAssessmentResult(
        framework=framework,
        total_controls=len(controls),
        pass_count=pass_count,
        partial_count=partial_count,
        fail_count=fail_count,
        unable_count=unable_count,
        score=round(score, 4),
        evaluations=evaluations,
    )
