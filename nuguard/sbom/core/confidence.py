"""
Confidence Aggregator for AIBOM Node Detection

Combines confidence scores from multiple sources:
- Regex detection confidence (deterministic, never hallucinates)
- LLM analysis confidence (semantic understanding, stored in node.metadata.extras)
- Evidence count bonus (multiple sources = higher confidence)
- Validation adjustments (penalties for issues)

Formula:
  final_confidence = min(1.0, base_confidence * evidence_multiplier) - validation_penalty

Where:
  base_confidence = (regex_confidence * 0.4) + (llm_confidence * 0.6)
  evidence_multiplier = 1.0 + (0.1 * min(extra_sources, 4))

Standalone module: no dependency on backend services.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from xelo.models import Node

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = float(os.environ.get("AISBOM_CONFIDENCE_THRESHOLD", "0.40"))
REGEX_WEIGHT = float(os.environ.get("AISBOM_REGEX_WEIGHT", "0.4"))
LLM_WEIGHT = float(os.environ.get("AISBOM_LLM_WEIGHT", "0.6"))
EVIDENCE_BONUS_PER_SOURCE = float(os.environ.get("AISBOM_EVIDENCE_BONUS", "0.1"))
MAX_EVIDENCE_BONUS_SOURCES = int(os.environ.get("AISBOM_MAX_EVIDENCE_SOURCES", "4"))

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ConfidenceBreakdown:
    """Breakdown of confidence calculation components."""
    regex_confidence: float = 0.0
    llm_confidence: float = 0.0
    evidence_count: int = 1
    evidence_sources: list[str] = field(default_factory=list)
    base_confidence: float = 0.0
    evidence_multiplier: float = 1.0
    validation_penalty: float = 0.0
    final_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "regex_confidence": round(self.regex_confidence, 4),
            "llm_confidence": round(self.llm_confidence, 4),
            "evidence_count": self.evidence_count,
            "evidence_sources": self.evidence_sources,
            "base_confidence": round(self.base_confidence, 4),
            "evidence_multiplier": round(self.evidence_multiplier, 4),
            "validation_penalty": round(self.validation_penalty, 4),
            "final_confidence": round(self.final_confidence, 4),
        }


@dataclass
class AggregationStats:
    """Statistics from a confidence aggregation pass."""
    total_nodes: int = 0
    filtered_count: int = 0
    avg_final_confidence: float = 0.0
    avg_regex_confidence: float = 0.0
    avg_llm_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_nodes": self.total_nodes,
            "filtered_count": self.filtered_count,
            "avg_final_confidence": round(self.avg_final_confidence, 4),
            "avg_regex_confidence": round(self.avg_regex_confidence, 4),
            "avg_llm_confidence": round(self.avg_llm_confidence, 4),
        }


# ---------------------------------------------------------------------------
# Core calculation (pure, no I/O)
# ---------------------------------------------------------------------------


def calculate_final_confidence(
    regex_confidence: float = 0.0,
    llm_confidence: float = 0.0,
    evidence_sources: list[str] | None = None,
    validation_penalty: float = 0.0,
    regex_weight: float = REGEX_WEIGHT,
    llm_weight: float = LLM_WEIGHT,
) -> ConfidenceBreakdown:
    """Calculate final confidence from multiple sources.

    Parameters
    ----------
    regex_confidence:
        Confidence from regex/AST pattern matching (0.0–1.0).
    llm_confidence:
        Confidence from LLM verification (0.0–1.0). Pass 0.0 when no LLM
        enrichment was performed.
    evidence_sources:
        List of evidence source descriptions (each additional source beyond
        the first adds a small bonus).
    validation_penalty:
        Penalty subtracted at the end, e.g. for validation errors (0.0–1.0).
    """
    evidence_sources = evidence_sources or []

    if regex_confidence > 0 and llm_confidence > 0:
        base = (regex_confidence * regex_weight) + (llm_confidence * llm_weight)
    elif regex_confidence > 0:
        base = regex_confidence * 0.8  # single-source penalty
    elif llm_confidence > 0:
        base = llm_confidence * 0.85  # single-source penalty
    else:
        base = 0.5

    extra_sources = max(0, len(evidence_sources) - 1)
    multiplier = 1.0 + (EVIDENCE_BONUS_PER_SOURCE * min(extra_sources, MAX_EVIDENCE_BONUS_SOURCES))

    final = max(0.0, min(1.0, base * multiplier) - validation_penalty)

    return ConfidenceBreakdown(
        regex_confidence=regex_confidence,
        llm_confidence=llm_confidence,
        evidence_count=len(evidence_sources),
        evidence_sources=evidence_sources,
        base_confidence=base,
        evidence_multiplier=multiplier,
        validation_penalty=validation_penalty,
        final_confidence=final,
    )


# ---------------------------------------------------------------------------
# Node-level aggregation
# ---------------------------------------------------------------------------


def _build_evidence_sources(node: Node) -> list[str]:
    """Derive evidence source descriptors from a Node's metadata."""
    sources: list[str] = []
    extras = node.metadata.extras

    if extras.get("evidence_kind"):
        sources.append(f"method:{extras['evidence_kind']}")
    if extras.get("llm_verified"):
        sources.append("llm:verified")
    if node.metadata.framework:
        sources.append(f"framework:{node.metadata.framework}")
    evidence_count = extras.get("evidence_count", 0)
    for i in range(min(int(evidence_count), 5)):
        sources.append(f"evidence:{i}")
    if node.confidence >= 0.7:
        sources.append("confidence:high")
    if not sources:
        sources.append("detection:pattern")
    return sources


def aggregate_node_confidence(
    nodes: list[Node],
    threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[list[Node], AggregationStats]:
    """Aggregate confidence for all nodes and filter by threshold.

    LLM confidence is read from ``node.metadata.extras["llm_confidence"]``
    (written by ``apply_verification_results`` in ``verification.py``).
    Regex confidence is read from ``node.metadata.extras["regex_confidence"]``
    if present; otherwise the node's current ``confidence`` is treated as
    the regex-only score.

    Returns ``(filtered_nodes, stats)``.
    """
    if not nodes:
        return [], AggregationStats()

    stats = AggregationStats(total_nodes=len(nodes))
    result_nodes: list[Node] = []
    total_final = total_regex = total_llm = 0.0

    for node in nodes:
        extras = node.metadata.extras
        regex_confidence = float(extras.get("regex_confidence") or node.confidence)
        llm_confidence = float(extras.get("llm_confidence") or 0.0)
        validation_penalty = 0.0
        if extras.get("validation_errors"):
            validation_penalty = min(0.3, len(extras["validation_errors"]) * 0.1)

        evidence_sources = _build_evidence_sources(node)
        breakdown = calculate_final_confidence(
            regex_confidence=regex_confidence,
            llm_confidence=llm_confidence,
            evidence_sources=evidence_sources,
            validation_penalty=validation_penalty,
        )

        total_final += breakdown.final_confidence
        total_regex += breakdown.regex_confidence
        total_llm += breakdown.llm_confidence

        node.confidence = breakdown.final_confidence
        node.metadata.extras["confidence_breakdown"] = breakdown.to_dict()

        if breakdown.final_confidence >= threshold:
            result_nodes.append(node)
        else:
            stats.filtered_count += 1

    n = len(nodes)
    stats.avg_final_confidence = total_final / n
    stats.avg_regex_confidence = total_regex / n
    stats.avg_llm_confidence = total_llm / n

    return result_nodes, stats


def combine_duplicate_confidences(confidences: list[float]) -> float:
    """Combine multiple confidence scores for the same entity.

    Uses diminishing-returns formula so the combined score never exceeds 1.0.
    """
    if not confidences:
        return 0.0
    if len(confidences) == 1:
        return confidences[0]
    sorted_conf = sorted(confidences, reverse=True)
    combined = sorted_conf[0]
    for i, conf in enumerate(sorted_conf[1:], start=1):
        combined = min(1.0, combined + conf * (0.5 ** i) * (1 - combined))
    return combined


def get_confidence_config() -> dict[str, Any]:
    """Return current confidence aggregation configuration."""
    return {
        "threshold": CONFIDENCE_THRESHOLD,
        "regex_weight": REGEX_WEIGHT,
        "llm_weight": LLM_WEIGHT,
        "evidence_bonus_per_source": EVIDENCE_BONUS_PER_SOURCE,
        "max_evidence_bonus_sources": MAX_EVIDENCE_BONUS_SOURCES,
    }
