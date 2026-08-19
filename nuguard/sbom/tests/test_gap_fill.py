"""Tests for the LLM gap-fill category gating in nuguard.sbom.core.gap_fill.

Focused on _identify_absent_categories, since that's the piece extended in
this change (API_ENDPOINT added as a fallback-only category; AGENT relaxed
from a blanket exclusion to a narrower "hand-rolled orchestration only"
rule) — the LLM call itself is exercised indirectly via existing
integration coverage and is not re-tested here.
"""

from __future__ import annotations

from nuguard.sbom.core.gap_fill import _identify_absent_categories
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType


def _node(component_type: ComponentType, name: str = "x", confidence: float = 0.9, **extras) -> Node:
    return Node(
        name=name,
        component_type=component_type,
        confidence=confidence,
        metadata=NodeMetadata(extras=extras),
    )


def test_api_endpoint_gap_filled_when_absent() -> None:
    """A doc with zero API_ENDPOINT nodes should gap-fill that category —
    this is the fallback net for web frameworks with no AST adapter yet
    (e.g. NestJS)."""
    doc = AiSbomDocument(target=".", nodes=[_node(ComponentType.MODEL)])
    assert ComponentType.API_ENDPOINT in _identify_absent_categories(doc)


def test_api_endpoint_skipped_when_already_found() -> None:
    """Once the deterministic pass finds >=1 endpoint (a real adapter
    exists), gap-fill must not re-run for this category."""
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.API_ENDPOINT, name="GET /health", confidence=0.9)],
    )
    assert ComponentType.API_ENDPOINT not in _identify_absent_categories(doc)


def test_agent_gap_filled_when_hand_rolled_no_framework() -> None:
    """Zero AGENT nodes and zero recognized AI-framework nodes means the
    orchestration (if any) is genuinely hand-rolled — this is exactly the
    case a framework-based adapter could never have caught, so gap-fill
    should be allowed to look for it."""
    doc = AiSbomDocument(target=".", nodes=[_node(ComponentType.MODEL)])
    assert ComponentType.AGENT in _identify_absent_categories(doc)


def test_agent_skipped_when_framework_node_present() -> None:
    """A recognized AI-framework node (e.g. langgraph) means deterministic
    AGENT detection already has ~97% recall — gap-fill must not re-trigger
    just because that AGENT node's own confidence is below the 0.65 bar."""
    doc = AiSbomDocument(
        target=".",
        nodes=[
            _node(ComponentType.FRAMEWORK, name="LangGraph", confidence=0.9, adapter="langgraph"),
        ],
    )
    assert ComponentType.AGENT not in _identify_absent_categories(doc)


def test_agent_skipped_when_agent_node_already_present() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.AGENT, name="Orchestrator", confidence=0.9)],
    )
    assert ComponentType.AGENT not in _identify_absent_categories(doc)


def test_agent_gap_fill_checks_framework_metadata_not_just_adapter() -> None:
    """The framework marker check must look at metadata.framework and
    canonical_name too, not just the adapter extras key, since different
    adapters populate different subsets of these fields."""
    doc = AiSbomDocument(
        target=".",
        nodes=[
            Node(
                name="crewai runtime",
                component_type=ComponentType.FRAMEWORK,
                confidence=0.9,
                metadata=NodeMetadata(framework="crewai"),
            )
        ],
    )
    assert ComponentType.AGENT not in _identify_absent_categories(doc)


def test_guardrail_and_privilege_never_gap_filled() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    absent = _identify_absent_categories(doc)
    assert ComponentType.GUARDRAIL not in absent
    assert ComponentType.PRIVILEGE not in absent
