"""Scoring utilities for compliance evaluation.

Ported from assessment_service/core/scoring.py.  All SQLAlchemy references
have been removed; ComplianceResult comes from nuguard.models.policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from nuguard.common.logging import get_logger
from nuguard.models.policy import ComplianceResult

_log = get_logger(__name__)

# Named threshold constants — controls the FAIL / PARTIAL / PASS bands.
FAIL_THRESHOLD = 0.2
PARTIAL_THRESHOLD = 0.8


@dataclass
class ValidationResult:
    """Result of validating a set of control evaluations for consistency."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def hash_payload(payload: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 hex digest for a JSON-serialisable dict."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def score_to_result(
    score: float,
    fail_threshold: float = FAIL_THRESHOLD,
    partial_threshold: float = PARTIAL_THRESHOLD,
) -> ComplianceResult:
    """Map a numeric score in [0, 1] to a ComplianceResult.

    Args:
        score: Numeric compliance score.
        fail_threshold: Scores strictly below this value map to FAIL.
        partial_threshold: Scores at or above this map to PASS; in between →
            PARTIAL.

    Returns:
        The corresponding ComplianceResult enum member.
    """
    if score < fail_threshold:
        return ComplianceResult.FAIL
    if score < partial_threshold:
        return ComplianceResult.PARTIAL
    return ComplianceResult.PASS


def safe_float(value: Any, default: float) -> float:
    """Coerce *value* to float, returning *default* on any failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return default


def validate_results(
    results: list[Any],
    controls: list[Any],
) -> ValidationResult:
    """Check a set of control evaluations for consistency.

    Simplified compared to the full assessment-service version — only checks
    for duplicate control IDs and score/result consistency (no DB-level
    aggregate count checks).

    Args:
        results: List of ControlEvaluation (or duck-typed objects with
            ``control.id``, ``result``, and ``score`` attributes).
        controls: List of ComplianceControl objects used as the expected set.

    Returns:
        A ValidationResult with any errors or warnings found.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ---- Duplicate control IDs ----------------------------------------
    seen: dict[Any, int] = {}
    for r in results:
        cid = getattr(getattr(r, "control", None), "id", None)
        seen[cid] = seen.get(cid, 0) + 1
    duplicates = {cid: count for cid, count in seen.items() if count > 1}
    if duplicates:
        errors.append(f"Duplicate control evaluations: {duplicates}")

    # ---- Score / result consistency -----------------------------------
    exempt = {ComplianceResult.NOT_APPLICABLE, ComplianceResult.UNABLE_TO_ASSESS}
    for r in results:
        score = safe_float(getattr(r, "score", 0.0), 0.0)
        result = getattr(r, "result", None)
        cid = getattr(getattr(r, "control", None), "id", "unknown")

        if result not in exempt:
            if score < FAIL_THRESHOLD and result != ComplianceResult.FAIL:
                warnings.append(
                    f"Control {cid}: score {score:.2f} < {FAIL_THRESHOLD} "
                    f"but result is {getattr(result, 'value', str(result))}, expected FAIL"
                )
            if score >= PARTIAL_THRESHOLD and result != ComplianceResult.PASS:
                warnings.append(
                    f"Control {cid}: score {score:.2f} >= {PARTIAL_THRESHOLD} "
                    f"but result is {getattr(result, 'value', str(result))}, expected PASS"
                )

    is_valid = len(errors) == 0
    return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)
