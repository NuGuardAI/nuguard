"""Tests for nuguard.behavior.sbom_graph.SbomGraph."""
from __future__ import annotations

import uuid

import pytest

from nuguard.behavior.sbom_graph import SbomGraph
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import AccessType, ComponentType, RelationshipType

_NS = uuid.NAMESPACE_URL


def _node(name: str, ctype: ComponentType) -> Node:
    nid = uuid.uuid5(_NS, f"{ctype.value}/{name}")
    return Node(id=nid, name=name, component_type=ctype, confidence=1.0, metadata=NodeMetadata())


def _edge(src: Node, tgt: Node, rel: RelationshipType, access_type: AccessType | None = None) -> Edge:
    return Edge(source=src.id, target=tgt.id, relationship_type=rel, access_type=access_type)


def _make_sbom(*nodes: Node, edges: list[Edge] | None = None) -> AiSbomDocument:
    return AiSbomDocument(target="./app", nodes=list(nodes), edges=edges or [])


# ---------------------------------------------------------------------------
# Basic lookup
# ---------------------------------------------------------------------------


class TestNodesOfType:
    def test_returns_matching_nodes(self) -> None:
        agent = _node("AgentA", ComponentType.AGENT)
        tool = _node("ToolA", ComponentType.TOOL)
        sbom = _make_sbom(agent, tool)
        g = SbomGraph(sbom)
        assert [n.name for n in g.nodes_of_type("AGENT")] == ["AgentA"]
        assert [n.name for n in g.nodes_of_type("TOOL")] == ["ToolA"]

    def test_empty_when_no_match(self) -> None:
        agent = _node("AgentA", ComponentType.AGENT)
        sbom = _make_sbom(agent)
        g = SbomGraph(sbom)
        assert g.nodes_of_type("DATASTORE") == []

    def test_case_insensitive(self) -> None:
        agent = _node("AgentA", ComponentType.AGENT)
        sbom = _make_sbom(agent)
        g = SbomGraph(sbom)
        assert len(g.nodes_of_type("agent")) == 1


class TestTargetsAndSources:
    def test_targets_returns_connected_nodes(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        edge = _edge(agent, tool, RelationshipType.CALLS)
        sbom = _make_sbom(agent, tool, edges=[edge])
        g = SbomGraph(sbom)
        result = g.targets(agent.id, "CALLS")
        assert len(result) == 1
        assert result[0].name == "Tool"

    def test_sources_returns_upstream_nodes(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        edge = _edge(agent, tool, RelationshipType.CALLS)
        sbom = _make_sbom(agent, tool, edges=[edge])
        g = SbomGraph(sbom)
        result = g.sources(tool.id, "CALLS")
        assert len(result) == 1
        assert result[0].name == "Agent"

    def test_returns_empty_for_unknown_id(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        sbom = _make_sbom(agent)
        g = SbomGraph(sbom)
        assert g.targets(uuid.uuid4(), "CALLS") == []

    def test_does_not_cross_rel_types(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        edge = _edge(agent, tool, RelationshipType.CALLS)
        sbom = _make_sbom(agent, tool, edges=[edge])
        g = SbomGraph(sbom)
        assert g.targets(agent.id, "ACCESSES") == []


# ---------------------------------------------------------------------------
# BFS reachable_of_type
# ---------------------------------------------------------------------------


class TestReachableOfType:
    def test_finds_indirect_node(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        sbom = _make_sbom(agent, tool, ds, edges=[
            _edge(agent, tool, RelationshipType.CALLS),
            _edge(tool, ds, RelationshipType.ACCESSES),
        ])
        g = SbomGraph(sbom)
        result = g.reachable_of_type(agent.id, ["CALLS", "ACCESSES"], "DATASTORE", max_depth=3)
        assert any(n.name == "DB" for n in result)

    def test_respects_depth_limit(self) -> None:
        a = _node("A", ComponentType.AGENT)
        b = _node("B", ComponentType.TOOL)
        c = _node("C", ComponentType.DATASTORE)
        sbom = _make_sbom(a, b, c, edges=[
            _edge(a, b, RelationshipType.CALLS),
            _edge(b, c, RelationshipType.ACCESSES),
        ])
        g = SbomGraph(sbom)
        # max_depth=1: A→B is depth 1, B→C needs depth 2
        result = g.reachable_of_type(a.id, ["CALLS", "ACCESSES"], "DATASTORE", max_depth=1)
        assert result == []

    def test_does_not_return_start_node(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        sbom = _make_sbom(agent)
        g = SbomGraph(sbom)
        result = g.reachable_of_type(agent.id, ["CALLS"], "AGENT")
        assert all(n.id != agent.id for n in result)


# ---------------------------------------------------------------------------
# accesses_paths
# ---------------------------------------------------------------------------


class TestAccessesPaths:
    def test_direct_access_path(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        ds = _node("DB", ComponentType.DATASTORE)
        edge = _edge(agent, ds, RelationshipType.ACCESSES, AccessType.READ)
        sbom = _make_sbom(agent, ds, edges=[edge])
        g = SbomGraph(sbom)
        paths = g.accesses_paths(agent.id)
        assert len(paths) == 1
        intermediary, ds_node, access_type = paths[0]
        assert intermediary is None
        assert ds_node.name == "DB"
        assert str(access_type.value if hasattr(access_type, "value") else access_type) == "read"

    def test_transitive_access_path(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        sbom = _make_sbom(agent, tool, ds, edges=[
            _edge(agent, tool, RelationshipType.CALLS),
            _edge(tool, ds, RelationshipType.ACCESSES, AccessType.WRITE),
        ])
        g = SbomGraph(sbom)
        paths = g.accesses_paths(agent.id)
        assert len(paths) == 1
        intermediary, ds_node, access_type = paths[0]
        assert intermediary is not None and intermediary.name == "Tool"
        assert ds_node.name == "DB"
        at = str(access_type.value if hasattr(access_type, "value") else access_type)
        assert at == "write"

    def test_delegates_to_path(self) -> None:
        src = _node("SrcAgent", ComponentType.AGENT)
        dst = _node("DstAgent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        sbom = _make_sbom(src, dst, tool, ds, edges=[
            _edge(src, dst, RelationshipType.DELEGATES_TO),
            _edge(dst, tool, RelationshipType.CALLS),
            _edge(tool, ds, RelationshipType.ACCESSES, AccessType.READWRITE),
        ])
        g = SbomGraph(sbom)
        paths = g.accesses_paths(src.id)
        assert any(ds_node.name == "DB" for _, ds_node, _ in paths)

    def test_no_paths_when_no_accesses(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        sbom = _make_sbom(agent, tool, edges=[
            _edge(agent, tool, RelationshipType.CALLS),
        ])
        g = SbomGraph(sbom)
        assert g.accesses_paths(agent.id) == []

    def test_deduplicates_paths(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool1 = _node("Tool1", ComponentType.TOOL)
        tool2 = _node("Tool2", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        # Two tools both access the same datastore
        sbom = _make_sbom(agent, tool1, tool2, ds, edges=[
            _edge(agent, tool1, RelationshipType.CALLS),
            _edge(agent, tool2, RelationshipType.CALLS),
            _edge(tool1, ds, RelationshipType.ACCESSES, AccessType.READ),
            _edge(tool2, ds, RelationshipType.ACCESSES, AccessType.READ),
        ])
        g = SbomGraph(sbom)
        paths = g.accesses_paths(agent.id)
        ds_nodes = [ds_node.name for _, ds_node, _ in paths]
        # Both paths should be returned (different intermediaries)
        assert ds_nodes.count("DB") == 2


# ---------------------------------------------------------------------------
# has_protection
# ---------------------------------------------------------------------------


class TestHasProtection:
    def test_guardrail_protects(self) -> None:
        guardrail = _node("Guard", ComponentType.GUARDRAIL)
        tool = _node("Tool", ComponentType.TOOL)
        sbom = _make_sbom(guardrail, tool, edges=[
            _edge(guardrail, tool, RelationshipType.PROTECTS),
        ])
        g = SbomGraph(sbom)
        assert g.has_protection(tool.id) is True

    def test_auth_protects(self) -> None:
        auth = _node("AuthLayer", ComponentType.AUTH)
        ep = _node("/api", ComponentType.API_ENDPOINT)
        sbom = _make_sbom(auth, ep, edges=[
            _edge(auth, ep, RelationshipType.PROTECTS),
        ])
        g = SbomGraph(sbom)
        assert g.has_protection(ep.id) is True

    def test_unprotected_node(self) -> None:
        tool = _node("Tool", ComponentType.TOOL)
        sbom = _make_sbom(tool)
        g = SbomGraph(sbom)
        assert g.has_protection(tool.id) is False

    def test_calls_edge_does_not_count_as_protection(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        tool = _node("Tool", ComponentType.TOOL)
        sbom = _make_sbom(agent, tool, edges=[
            _edge(agent, tool, RelationshipType.CALLS),
        ])
        g = SbomGraph(sbom)
        assert g.has_protection(tool.id) is False
