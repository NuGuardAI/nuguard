"""Regression tests for issue #511: node_counts summaries must exclude
llm_soft_rejected nodes, same as analysis/policy/behavior/redteam. Before
this fix, a soft-rejected node (kept in the SBOM for provenance but flagged
as a likely false positive) was still counted as if verified."""
from __future__ import annotations

from nuguard.common.auto_sbom_enricher import _ensure_summary_node_counts
from nuguard.sbom.core.gap_fill.rounds import apply_discovery_results
from nuguard.sbom.models import AiSbomDocument, Node, ScanSummary
from nuguard.sbom.types import ComponentType


def _soft_rejected_node() -> Node:
    n = Node(name="mock-model", component_type=ComponentType.MODEL, confidence=0.5)
    n.metadata.extras["llm_soft_rejected"] = True
    return n


def test_ensure_summary_node_counts_excludes_soft_rejected():
    real = Node(name="real-model", component_type=ComponentType.MODEL, confidence=0.9)
    sbom = AiSbomDocument(
        target="unit-test",
        nodes=[real, _soft_rejected_node()],
        summary=ScanSummary(),
    )

    _ensure_summary_node_counts(sbom)

    assert sbom.summary.node_counts == {"MODEL": 1}


def test_apply_discovery_results_node_counts_excludes_soft_rejected():
    existing = Node(name="real-agent", component_type=ComponentType.AGENT, confidence=0.9)
    sbom = AiSbomDocument(target="unit-test", nodes=[existing], summary=ScanSummary())

    apply_discovery_results(sbom, [_soft_rejected_node()])

    assert sbom.summary.node_counts == {"AGENT": 1}
