"""Tests for nuguard.sbom.core.relationship_graph.

Covers:
  _graph_context      — provenance/access_type/risk-tag surfacing, size-cap
                         truncation with priority ordering
  build_relationship_graph_with_llm — narrative prompt content (stubbed LLM)
"""

from __future__ import annotations

import uuid

import pytest

from nuguard.sbom.core.relationship_graph import (
    _MAX_CONTEXT_LINES,
    _graph_context,
    build_relationship_graph_with_llm,
)
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import AccessType, ComponentType, RelationshipType

_NS = uuid.NAMESPACE_URL


class _StubClient:
    def __init__(self, response: str = "Stub narrative.") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, prompt: str, system: str) -> str:
        self.calls.append((system, prompt))
        return self.response


def _node(
    name: str,
    ctype: ComponentType,
    *,
    confidence: float = 0.9,
    **risk_attrs: bool,
) -> Node:
    node_id = uuid.uuid5(_NS, f"{ctype}/{name}")
    return Node(
        id=node_id,
        name=name,
        component_type=ctype,
        confidence=confidence,
        metadata=NodeMetadata(**risk_attrs),
    )


def _edge(
    src: Node,
    tgt: Node,
    rel: RelationshipType,
    *,
    derivation: str = "hint",
    confidence: float | None = None,
    access_type: AccessType | None = None,
) -> Edge:
    return Edge(
        source=src.id,
        target=tgt.id,
        relationship_type=rel,
        access_type=access_type,
        derivation=derivation,
        confidence=confidence,
    )


class TestGraphContextProvenance:
    def test_hint_edge_has_no_heuristic_marker(self) -> None:
        agent = _node("Orchestrator", ComponentType.AGENT)
        tool = _node("Search", ComponentType.TOOL)
        doc = AiSbomDocument(
            target=".",
            nodes=[agent, tool],
            edges=[_edge(agent, tool, RelationshipType.CALLS, derivation="hint")],
        )

        context = _graph_context(doc)

        assert "CALLS" in context
        assert "heuristic" not in context

    def test_fallback_edge_shows_heuristic_and_confidence(self) -> None:
        agent = _node("Orchestrator", ComponentType.AGENT)
        model = _node("gpt-4o", ComponentType.MODEL)
        doc = AiSbomDocument(
            target=".",
            nodes=[agent, model],
            edges=[
                _edge(
                    agent, model, RelationshipType.USES,
                    derivation="fallback_heuristic", confidence=0.5,
                )
            ],
        )

        context = _graph_context(doc)

        assert "heuristic" in context
        assert "confidence=0.50" in context

    def test_access_type_surfaced_on_edge(self) -> None:
        tool = _node("Search", ComponentType.TOOL)
        ds = _node("UsersDB", ComponentType.DATASTORE)
        doc = AiSbomDocument(
            target=".",
            nodes=[tool, ds],
            edges=[
                _edge(
                    tool, ds, RelationshipType.ACCESSES,
                    derivation="hint", access_type=AccessType.WRITE,
                )
            ],
        )

        context = _graph_context(doc)

        assert "ACCESSES:write" in context

    def test_risk_tags_appear_on_node_labels(self) -> None:
        tool = _node(
            "PatientLookup", ComponentType.TOOL,
            no_auth_required=True, sql_injectable=True,
        )
        agent = _node("Orchestrator", ComponentType.AGENT)
        doc = AiSbomDocument(
            target=".",
            nodes=[agent, tool],
            edges=[_edge(agent, tool, RelationshipType.CALLS)],
        )

        context = _graph_context(doc)

        assert "no-auth-required" in context
        assert "SQL-injectable" in context

    def test_no_risk_tags_when_attrs_unset(self) -> None:
        agent = _node("Orchestrator", ComponentType.AGENT)
        tool = _node("Search", ComponentType.TOOL)
        doc = AiSbomDocument(
            target=".",
            nodes=[agent, tool],
            edges=[_edge(agent, tool, RelationshipType.CALLS)],
        )

        context = _graph_context(doc)

        assert "Orchestrator (AGENT) --" in context
        assert "Search (TOOL) [" not in context

    def test_isolated_node_included(self) -> None:
        lone = _node("OrphanTool", ComponentType.TOOL)
        doc = AiSbomDocument(target=".", nodes=[lone], edges=[])

        context = _graph_context(doc)

        assert "OrphanTool" in context
        assert "no connections" in context

    def test_empty_graph_returns_placeholder(self) -> None:
        doc = AiSbomDocument(target=".", nodes=[], edges=[])
        assert _graph_context(doc) == "No relationships detected."


class TestGraphContextSizeCap:
    def test_within_cap_no_truncation_note(self) -> None:
        agent = _node("Orchestrator", ComponentType.AGENT)
        tools = [_node(f"Tool{i}", ComponentType.TOOL) for i in range(5)]
        doc = AiSbomDocument(
            target=".",
            nodes=[agent, *tools],
            edges=[_edge(agent, t, RelationshipType.CALLS) for t in tools],
        )

        context = _graph_context(doc)

        assert "omitted" not in context
        assert context.count("-->") == 5

    def test_over_cap_truncates_and_prioritizes_risk_and_hint(self) -> None:
        agent = _node("Orchestrator", ComponentType.AGENT)
        risky_tool = _node("PatientLookup", ComponentType.TOOL, no_auth_required=True)
        # One evidence-backed edge to a risk-flagged node.
        risky_edge = _edge(agent, risky_tool, RelationshipType.CALLS, derivation="hint")

        # Flood with low-priority fallback edges to force truncation.
        filler_tools = [_node(f"FillerTool{i}", ComponentType.TOOL) for i in range(_MAX_CONTEXT_LINES + 20)]
        filler_edges = [
            _edge(agent, t, RelationshipType.CALLS, derivation="fallback_heuristic", confidence=0.3)
            for t in filler_tools
        ]

        doc = AiSbomDocument(
            target=".",
            nodes=[agent, risky_tool, *filler_tools],
            edges=[risky_edge, *filler_edges],
        )

        context = _graph_context(doc)
        lines = context.split("\n")

        assert lines[-1].endswith("omitted")
        # The risk-flagged, evidence-backed edge must survive truncation —
        # it should sort ahead of the low-priority heuristic filler.
        assert "PatientLookup" in context
        assert "no-auth-required" in context


class TestBuildRelationshipGraphWithLlm:
    @pytest.mark.asyncio
    async def test_prompt_instructs_hedging_and_risk_callouts(self) -> None:
        agent = _node("Orchestrator", ComponentType.AGENT)
        tool = _node("Search", ComponentType.TOOL)
        doc = AiSbomDocument(
            target=".",
            nodes=[agent, tool],
            edges=[
                _edge(
                    agent, tool, RelationshipType.CALLS,
                    derivation="fallback_heuristic", confidence=0.5,
                )
            ],
        )
        client = _StubClient()

        result = await build_relationship_graph_with_llm(doc, client)

        assert client.calls, "Expected an LLM call to be made"
        system, prompt = client.calls[0]
        assert "heuristic" in system.lower()
        assert "risk tag" in system.lower() or "risk-tag" in system.lower()
        assert "heuristic" in prompt.lower()
        assert "Stub narrative." in result
        assert "```mermaid" in result

    @pytest.mark.asyncio
    async def test_falls_back_to_diagram_only_on_llm_error(self) -> None:
        agent = _node("Orchestrator", ComponentType.AGENT)
        tool = _node("Search", ComponentType.TOOL)
        doc = AiSbomDocument(
            target=".",
            nodes=[agent, tool],
            edges=[_edge(agent, tool, RelationshipType.CALLS)],
        )

        class _FailingClient:
            async def complete(self, *, prompt: str, system: str) -> str:
                raise RuntimeError("boom")

        result = await build_relationship_graph_with_llm(doc, _FailingClient())

        assert "```mermaid" in result
