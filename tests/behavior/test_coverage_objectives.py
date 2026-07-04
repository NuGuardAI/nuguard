"""Tests for nuguard.behavior.planner.build_coverage_objectives.

Verifies that every ComponentType and RelationshipType value produces a
BehaviorCoverageObjective with the expected behavior_mode.
"""
from __future__ import annotations

import uuid

import pytest

from nuguard.behavior.planner import build_coverage_objectives
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.NAMESPACE_URL


def _node(name: str, ctype: ComponentType) -> Node:
    nid = uuid.uuid5(_NS, f"{ctype.value}/{name}")
    return Node(id=nid, name=name, component_type=ctype, confidence=1.0, metadata=NodeMetadata())


def _edge(src: Node, tgt: Node, rel: RelationshipType) -> Edge:
    return Edge(source=src.id, target=tgt.id, relationship_type=rel)


# ---------------------------------------------------------------------------
# Node behavior_mode classification
# ---------------------------------------------------------------------------


_NODE_MODE_CASES = [
    (ComponentType.AGENT, "dynamic"),
    (ComponentType.TOOL, "dynamic"),
    (ComponentType.GUARDRAIL, "dynamic"),
    (ComponentType.DATASTORE, "dynamic"),
    (ComponentType.API_ENDPOINT, "dynamic"),
    (ComponentType.PROMPT, "dynamic"),
    (ComponentType.AUTH, "metadata_only"),
    (ComponentType.PRIVILEGE, "metadata_only"),
    (ComponentType.MODEL, "metadata_only"),
    (ComponentType.FRAMEWORK, "metadata_only"),
    (ComponentType.IAM, "not_behavior_exercisable"),
    (ComponentType.DEPLOYMENT, "not_behavior_exercisable"),
    (ComponentType.CONTAINER_IMAGE, "not_behavior_exercisable"),
]


@pytest.mark.parametrize("ctype, expected_mode", _NODE_MODE_CASES)
def test_node_behavior_mode(ctype: ComponentType, expected_mode: str) -> None:
    """Every ComponentType maps to a specific behavior_mode."""
    node = _node(ctype.value, ctype)
    sbom = AiSbomDocument(target="./app", nodes=[node], edges=[])
    objectives = build_coverage_objectives(sbom)
    node_objs = [o for o in objectives if o.surface_type == "node" and o.node_type == ctype.value]
    assert node_objs, f"No objective found for {ctype.value}"
    assert node_objs[0].behavior_mode == expected_mode, (
        f"{ctype.value}: expected {expected_mode!r}, got {node_objs[0].behavior_mode!r}"
    )


# ---------------------------------------------------------------------------
# One objective per node
# ---------------------------------------------------------------------------


def test_every_node_gets_objective() -> None:
    nodes = [_node(ct.value, ct) for ct in ComponentType]
    sbom = AiSbomDocument(target="./app", nodes=nodes, edges=[])
    objectives = build_coverage_objectives(sbom)
    node_objs = [o for o in objectives if o.surface_type == "node"]
    assert len(node_objs) == len(nodes)


# ---------------------------------------------------------------------------
# Edge behavior_mode classification
# ---------------------------------------------------------------------------


_EDGE_MODE_CASES = [
    (RelationshipType.CALLS, "dynamic"),
    (RelationshipType.ACCESSES, "dynamic"),
    (RelationshipType.DELEGATES_TO, "dynamic"),
    (RelationshipType.PROTECTS, "static"),
    (RelationshipType.DEPLOYS, "static"),
]


@pytest.mark.parametrize("rel, expected_mode", _EDGE_MODE_CASES)
def test_edge_behavior_mode(rel: RelationshipType, expected_mode: str) -> None:
    """Every RelationshipType maps to a specific behavior_mode."""
    src = _node("Source", ComponentType.AGENT)
    tgt = _node("Target", ComponentType.TOOL)
    e = _edge(src, tgt, rel)
    sbom = AiSbomDocument(target="./app", nodes=[src, tgt], edges=[e])
    objectives = build_coverage_objectives(sbom)
    edge_objs = [o for o in objectives if o.surface_type == "edge" and o.relationship_type == rel.value]
    assert edge_objs, f"No objective found for edge {rel.value}"
    assert edge_objs[0].behavior_mode == expected_mode


def test_uses_edge_to_prompt_is_dynamic() -> None:
    """USES → PROMPT edge is dynamic (exercised indirectly via agent scenarios)."""
    agent = _node("Agent", ComponentType.AGENT)
    prompt = _node("SysPrompt", ComponentType.PROMPT)
    e = _edge(agent, prompt, RelationshipType.USES)
    sbom = AiSbomDocument(target="./app", nodes=[agent, prompt], edges=[e])
    objectives = build_coverage_objectives(sbom)
    edge_objs = [
        o for o in objectives
        if o.surface_type == "edge" and o.relationship_type == "USES"
    ]
    assert edge_objs
    assert edge_objs[0].behavior_mode == "dynamic"


def test_uses_edge_to_model_is_metadata_only() -> None:
    """USES → MODEL edge is metadata_only."""
    agent = _node("Agent", ComponentType.AGENT)
    model = _node("GPT4", ComponentType.MODEL)
    e = _edge(agent, model, RelationshipType.USES)
    sbom = AiSbomDocument(target="./app", nodes=[agent, model], edges=[e])
    objectives = build_coverage_objectives(sbom)
    edge_objs = [
        o for o in objectives
        if o.surface_type == "edge" and o.relationship_type == "USES"
    ]
    assert edge_objs
    assert edge_objs[0].behavior_mode == "metadata_only"


# ---------------------------------------------------------------------------
# One objective per edge
# ---------------------------------------------------------------------------


def test_every_edge_gets_objective() -> None:
    agent = _node("Agent", ComponentType.AGENT)
    tool = _node("Tool", ComponentType.TOOL)
    ds = _node("DB", ComponentType.DATASTORE)
    edges = [
        _edge(agent, tool, RelationshipType.CALLS),
        _edge(tool, ds, RelationshipType.ACCESSES),
    ]
    sbom = AiSbomDocument(target="./app", nodes=[agent, tool, ds], edges=edges)
    objectives = build_coverage_objectives(sbom)
    edge_objs = [o for o in objectives if o.surface_type == "edge"]
    assert len(edge_objs) == len(edges)


# ---------------------------------------------------------------------------
# Objective fields
# ---------------------------------------------------------------------------


def test_node_objective_fields() -> None:
    agent = _node("MyAgent", ComponentType.AGENT)
    sbom = AiSbomDocument(target="./app", nodes=[agent], edges=[])
    objectives = build_coverage_objectives(sbom)
    node_obj = next(o for o in objectives if o.surface_type == "node" and o.node_name == "MyAgent")
    assert node_obj.node_id == str(agent.id)
    assert node_obj.node_type == "AGENT"
    assert node_obj.status == "generated"
    assert node_obj.objective_id.startswith("node-")


def test_edge_objective_fields() -> None:
    agent = _node("Agent", ComponentType.AGENT)
    tool = _node("Tool", ComponentType.TOOL)
    e = _edge(agent, tool, RelationshipType.CALLS)
    sbom = AiSbomDocument(target="./app", nodes=[agent, tool], edges=[e])
    objectives = build_coverage_objectives(sbom)
    edge_obj = next(o for o in objectives if o.surface_type == "edge")
    assert edge_obj.edge_source == str(agent.id)
    assert edge_obj.edge_target == str(tool.id)
    assert edge_obj.relationship_type == "CALLS"
    assert edge_obj.behavior_mode == "dynamic"
    assert edge_obj.objective_id.startswith("edge-")


def test_empty_sbom_returns_empty() -> None:
    sbom = AiSbomDocument(target="./app", nodes=[], edges=[])
    assert build_coverage_objectives(sbom) == []
