"""CCD graph query engine — evaluates ComplianceControls against SBOM snapshots.

Each control may carry a CCD (Compliance Control Descriptor) with a list of
assertions.  This module executes those assertions against the AIBOM snapshot
and returns a ControlEvaluation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from nuguard.common.logging import get_logger
from nuguard.models.policy import (
    ComplianceControl,
    ComplianceResult,
    ControlEvaluation,
)
from nuguard.policy.ccd_parser import (
    AssertionType,
    CCDParser,
    ParsedCCD,
)
from nuguard.policy.scoring import safe_float, score_to_result
from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

_ccd_parser = CCDParser()

# Severity → numeric weight used when computing weighted mean scores.
_SEVERITY_WEIGHT: dict[str, float] = {
    "critical": 3.0,
    "high": 2.0,
    "medium": 1.0,
    "low": 1.0,
    "info": 0.5,
}


# ---------------------------------------------------------------------------
# Assertion evaluators
# ---------------------------------------------------------------------------


def _filter_nodes(
    nodes: list[dict[str, Any]], filter_dict: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return nodes whose properties match all key/value pairs in *filter_dict*."""
    if not filter_dict:
        return nodes
    result = []
    for node in nodes:
        match = True
        for k, v in filter_dict.items():
            node_val = node.get(k)
            if isinstance(v, bool):
                if bool(node_val) != v:
                    match = False
                    break
            elif node_val != v:
                match = False
                break
        if match:
            result.append(node)
    return result


def _resolve_node_bucket(
    snapshot_nodes: dict[str, list[dict[str, Any]]],
    node_type: str,
) -> list[dict[str, Any]]:
    """Return the snapshot bucket for *node_type* (case-insensitive, with pluralisation fallback)."""
    # Try exact, lowercase, then lowercase plural variants
    for key in [node_type, node_type.lower(), node_type.lower() + "s"]:
        if key in snapshot_nodes:
            return snapshot_nodes[key]
    return []


def _evaluate_exists(
    assertion: Any,
    snapshot_nodes: dict[str, list[dict[str, Any]]],
) -> float:
    """Return 1.0 when at least one matching node exists; else 0.0."""
    node_type = assertion.node_type or (
        (assertion.query or {}).get("filter", {}) or {}
    ).get("type", "")
    if not node_type:
        return 0.0
    bucket = _resolve_node_bucket(snapshot_nodes, node_type)
    flt = assertion.filter or {}
    if isinstance(assertion.query, dict):
        flt = {**flt, **((assertion.query.get("filter") or {}))}
    matched = _filter_nodes(bucket, flt)
    min_count = assertion.min_count or 1
    return 1.0 if len(matched) >= min_count else 0.0


def _evaluate_absence(
    assertion: Any,
    snapshot_nodes: dict[str, list[dict[str, Any]]],
) -> float:
    """Inverse of EXISTS — 1.0 when no matching node exists."""
    return 1.0 - _evaluate_exists(assertion, snapshot_nodes)


def _evaluate_count(
    assertion: Any,
    snapshot_nodes: dict[str, list[dict[str, Any]]],
) -> float:
    """Evaluate COUNT assertion: count nodes and apply comparison operator."""
    node_type = assertion.node_type or (
        (assertion.query or {}).get("filter", {}) or {}
    ).get("type", "")
    bucket = _resolve_node_bucket(snapshot_nodes, node_type) if node_type else []
    flt = assertion.filter or {}
    matched = _filter_nodes(bucket, flt)
    count = len(matched)

    operator = (assertion.operator or "gte").lower()
    threshold = safe_float(assertion.value, 0.0)

    if operator == "gte":
        return 1.0 if count >= threshold else count / max(threshold, 1)
    if operator == "lte":
        return 1.0 if count <= threshold else threshold / max(count, 1)
    if operator == "eq":
        return 1.0 if count == int(threshold) else 0.0
    if operator == "neq":
        return 1.0 if count != int(threshold) else 0.0
    # Threshold min/max fallback
    t_min = assertion.threshold_min
    t_max = assertion.threshold_max
    if t_min is not None and count < t_min:
        return count / t_min
    if t_max is not None and count > t_max:
        return t_max / count
    return 1.0


def _evaluate_attribute(
    assertion: Any,
    snapshot_nodes: dict[str, list[dict[str, Any]]],
) -> float:
    """Return 1.0 when at least one matching node has the expected attribute value."""
    node_type = assertion.node_type or ""
    bucket = _resolve_node_bucket(snapshot_nodes, node_type) if node_type else []
    flt = assertion.node_filter or assertion.filter or {}
    matched = _filter_nodes(bucket, flt)
    if not matched:
        return 0.0

    attr = assertion.attribute or assertion.property_path or ""
    expected = assertion.expected_value
    if not attr:
        return 0.0

    passing = [n for n in matched if n.get(attr) == expected]
    return len(passing) / len(matched)


def _evaluate_path(
    assertion: Any,
    doc: AiSbomDocument,
) -> float:
    """Return 1.0 when a path exists between the from/to node types via edges."""
    path_q = assertion.path_query or {}
    from_type = str((path_q.get("from") or {}).get("type", "")).upper()
    to_type = str((path_q.get("to") or {}).get("type", "")).upper()
    if not from_type or not to_type:
        return 0.0

    # Build adjacency: source_id → set of target_ids
    adjacency: dict[UUID, set[UUID]] = {}
    for edge in doc.edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)

    from_ids: set[UUID] = {
        n.id for n in doc.nodes
        if n.component_type.value.upper() == from_type
    }
    to_ids: set[UUID] = {
        n.id for n in doc.nodes
        if n.component_type.value.upper() == to_type
    }

    if not from_ids or not to_ids:
        return 0.0

    # BFS from each from_id
    max_depth = path_q.get("max_depth", 10)
    for start in from_ids:
        visited: set[UUID] = set()
        frontier = {start}
        for _ in range(max_depth):
            if not frontier:
                break
            if frontier & to_ids:
                return 1.0
            next_frontier: set[UUID] = set()
            for node_id in frontier:
                if node_id not in visited:
                    visited.add(node_id)
                    next_frontier.update(adjacency.get(node_id, set()))
            frontier = next_frontier - visited
    return 0.0


# ---------------------------------------------------------------------------
# Per-assertion dispatcher
# ---------------------------------------------------------------------------


def _evaluate_assertion(
    assertion: Any,
    snapshot_nodes: dict[str, list[dict[str, Any]]],
    doc: AiSbomDocument,
) -> float:
    """Evaluate a single Assertion and return a score in [0.0, 1.0]."""
    atype = assertion.type

    # Normalise aliases to canonical forms
    if atype in (AssertionType.EXISTS, AssertionType.MUST_EXIST):
        return _evaluate_exists(assertion, snapshot_nodes)
    if atype in (AssertionType.ABSENCE, AssertionType.MUST_NOT_EXIST):
        return _evaluate_absence(assertion, snapshot_nodes)
    if atype in (AssertionType.COUNT, AssertionType.COUNT_THRESHOLD):
        return _evaluate_count(assertion, snapshot_nodes)
    if atype in (AssertionType.ATTRIBUTE, AssertionType.PROPERTY_CONSTRAINT):
        return _evaluate_attribute(assertion, snapshot_nodes)
    if atype in (AssertionType.PATH, AssertionType.MUST_EXIST_ON_PATH):
        return _evaluate_path(assertion, doc)
    if atype == AssertionType.MUST_EXIST_PER_INSTANCE:
        # Simplified: check that the required node type exists at all
        return _evaluate_exists(assertion, snapshot_nodes)

    _log.warning("Unknown assertion type %s — skipping", atype)
    return 0.0


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------


def evaluate_control_from_sbom(
    control: ComplianceControl,
    snapshot: dict[str, Any],
    doc: AiSbomDocument,
) -> ControlEvaluation:
    """Evaluate a ComplianceControl against an AIBOM snapshot.

    Returns UNABLE_TO_ASSESS when:
    - The control is not ai_sbom_assessable.
    - The control has no CCD.

    Otherwise evaluates each CCD assertion, computes the weighted mean score,
    and maps it to a ComplianceResult via score_to_result().

    Args:
        control: The control to evaluate.
        snapshot: AIBOM snapshot dict from build_aibom_snapshot().
        doc: Original AiSbomDocument (needed for edge-traversal assertions).

    Returns:
        ControlEvaluation with result, score, evidence, gaps, and remediation.
    """
    if not control.ai_sbom_assessable or not control.ccd:
        return ControlEvaluation(
            control=control,
            result=ComplianceResult.UNABLE_TO_ASSESS,
            score=0.0,
            evidence=[],
            gaps=["Control is not AI-SBOM assessable or has no CCD."],
            remediation=control.fix_guidance,
        )

    try:
        parsed_ccd: ParsedCCD = _ccd_parser.parse(control.ccd)
    except ValueError as exc:
        _log.warning("Failed to parse CCD for control %s: %s", control.id, exc)
        return ControlEvaluation(
            control=control,
            result=ComplianceResult.UNABLE_TO_ASSESS,
            score=0.0,
            evidence=[],
            gaps=[f"CCD parse error: {exc}"],
            remediation=control.fix_guidance,
        )

    snapshot_nodes: dict[str, list[dict[str, Any]]] = snapshot.get("nodes", {})
    assertions = parsed_ccd.assertions

    if not assertions:
        return ControlEvaluation(
            control=control,
            result=ComplianceResult.UNABLE_TO_ASSESS,
            score=0.0,
            evidence=[],
            gaps=["CCD has no assertions to evaluate."],
            remediation=control.fix_guidance,
        )

    # Evaluate each assertion
    assertion_scores: list[tuple[float, float]] = []  # (score, weight)
    evidence: list[str] = []
    gaps: list[str] = []

    for assertion in assertions:
        score = _evaluate_assertion(assertion, snapshot_nodes, doc)
        weight = safe_float(getattr(assertion, "weight", 1.0), 1.0)
        assertion_scores.append((score, weight))

        desc = getattr(assertion, "description", "") or str(getattr(assertion, "type", ""))
        if score >= 0.8:
            evidence.append(f"PASS ({score:.2f}): {desc}")
        elif score < 0.2:
            gaps.append(f"FAIL ({score:.2f}): {desc}")
        else:
            gaps.append(f"PARTIAL ({score:.2f}): {desc}")

    # Weighted mean
    total_weight = sum(w for _, w in assertion_scores)
    if total_weight == 0.0:
        weighted_score = 0.0
    else:
        weighted_score = sum(s * w for s, w in assertion_scores) / total_weight

    result = score_to_result(weighted_score)

    remediation = control.fix_guidance if result != ComplianceResult.PASS else ""
    if result == ComplianceResult.PASS:
        gaps = []

    _log.debug(
        "evaluate_control control_id=%s score=%.3f result=%s assertions=%d",
        control.id,
        weighted_score,
        result.value,
        len(assertions),
    )

    return ControlEvaluation(
        control=control,
        result=result,
        score=round(weighted_score, 4),
        evidence=evidence,
        gaps=gaps,
        remediation=remediation,
    )
