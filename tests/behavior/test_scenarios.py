"""Tests for behavior scenario generation — focusing on coverage of AGENT nodes."""
from __future__ import annotations

import uuid

import pytest

from nuguard.behavior.models import BehaviorScenarioType, IntentProfile
from nuguard.behavior.scenarios import _tool_coverage_scenarios
from nuguard.sbom.models import (
    AiSbomDocument,
    Edge,
    EdgeRelationshipType,
    Node,
    NodeMetadata,
    NodeType,
)

_NS = uuid.NAMESPACE_URL


def _make_sbom(
    agent_names: list[str],
    tool_names: list[str],
) -> AiSbomDocument:
    """Build a minimal SBOM with AGENT→TOOL CALLS edges."""
    nodes: list[Node] = []
    edges: list[Edge] = []

    agent_ids: dict[str, uuid.UUID] = {}
    for name in agent_names:
        nid = uuid.uuid5(_NS, f"agent/{name}")
        agent_ids[name] = nid
        nodes.append(Node(
            id=nid,
            name=name,
            component_type=NodeType.AGENT,
            confidence=0.9,
            metadata=NodeMetadata(description=f"{name} handles customer requests"),
        ))

    for i, name in enumerate(tool_names):
        tid = uuid.uuid5(_NS, f"tool/{name}")
        nodes.append(Node(
            id=tid,
            name=name,
            component_type=NodeType.TOOL,
            confidence=0.9,
            metadata=NodeMetadata(description=f"{name} tool"),
        ))
        # Wire first agent → all tools
        if agent_names:
            edges.append(Edge(
                source=agent_ids[agent_names[0]],
                target=tid,
                relationship_type=EdgeRelationshipType.CALLS,
            ))

    return AiSbomDocument(target="./test-app", nodes=nodes, edges=edges)


def _make_intent(purpose: str = "test app") -> IntentProfile:
    return IntentProfile(app_purpose=purpose, core_capabilities=["help users"])


# ---------------------------------------------------------------------------
# Agent-level scenario emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_coverage_emits_agent_scenario():
    """_tool_coverage_scenarios must emit at least one AGENT-typed scenario."""
    sbom = _make_sbom(["MyAgent"], ["tool_a", "tool_b"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_scenarios = [
        s for s in scenarios
        if getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
        or s.target_component_type == "AGENT"
    ]
    assert agent_scenarios, "Expected at least one AGENT-type scenario from _tool_coverage_scenarios"


@pytest.mark.asyncio
async def test_tool_coverage_agent_scenario_targets_correct_agent():
    """The emitted agent scenario must target the actual agent name."""
    sbom = _make_sbom(["MyAgent"], ["tool_a"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_scenarios = [
        s for s in scenarios
        if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
            or s.target_component_type == "AGENT")
    ]
    assert any(s.target_component == "MyAgent" for s in agent_scenarios)


@pytest.mark.asyncio
async def test_tool_coverage_agent_scenario_scoped_correctly():
    """Agent scenario must set scoped_agents=[agent_name] and scoped_tools=[]."""
    sbom = _make_sbom(["MyAgent"], ["tool_a"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_sc = next(
        (s for s in scenarios
         if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
             or s.target_component_type == "AGENT")
         and s.target_component == "MyAgent"),
        None,
    )
    assert agent_sc is not None
    assert list(agent_sc.scoped_agents) == ["MyAgent"]
    assert list(agent_sc.scoped_tools) == []


@pytest.mark.asyncio
async def test_tool_coverage_no_agent_scenario_for_standalone_group():
    """Standalone tool groups (no agent) must not produce an AGENT-type scenario."""
    # SBOM with a tool that has no CALLS edge from any agent — becomes standalone
    tid = uuid.uuid5(_NS, "tool/orphan_tool")
    sbom = AiSbomDocument(
        target="./test-app",
        nodes=[Node(id=tid, name="orphan_tool", component_type=NodeType.TOOL, confidence=0.9)],
        edges=[],
    )
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_scenarios = [
        s for s in scenarios
        if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
            or s.target_component_type == "AGENT")
    ]
    assert not agent_scenarios, "Standalone tool groups must not emit AGENT-type scenarios"


@pytest.mark.asyncio
async def test_tool_coverage_multiple_agents_each_get_agent_scenario():
    """Each distinct real agent in the SBOM must get its own agent-level scenario."""
    agent1_id = uuid.uuid5(_NS, "agent/Agent1")
    agent2_id = uuid.uuid5(_NS, "agent/Agent2")
    tool1_id = uuid.uuid5(_NS, "tool/tool1")
    tool2_id = uuid.uuid5(_NS, "tool/tool2")

    sbom = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(id=agent1_id, name="Agent1", component_type=NodeType.AGENT, confidence=0.9),
            Node(id=agent2_id, name="Agent2", component_type=NodeType.AGENT, confidence=0.9),
            Node(id=tool1_id, name="tool1", component_type=NodeType.TOOL, confidence=0.9),
            Node(id=tool2_id, name="tool2", component_type=NodeType.TOOL, confidence=0.9),
        ],
        edges=[
            Edge(source=agent1_id, target=tool1_id, relationship_type=EdgeRelationshipType.CALLS),
            Edge(source=agent2_id, target=tool2_id, relationship_type=EdgeRelationshipType.CALLS),
        ],
    )
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_targets = {
        s.target_component
        for s in scenarios
        if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
            or s.target_component_type == "AGENT")
    }
    assert "Agent1" in agent_targets
    assert "Agent2" in agent_targets


@pytest.mark.asyncio
async def test_tool_coverage_agent_scenario_is_agent_coverage_type():
    """Agent scenarios emitted by _tool_coverage_scenarios use AGENT_COVERAGE type
    (from _deterministic_agent_scenario), which the runner handles correctly."""
    sbom = _make_sbom(["MyAgent"], ["tool_a"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_sc = next(
        (s for s in scenarios
         if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
             or s.target_component_type == "AGENT")),
        None,
    )
    assert agent_sc is not None
    stype = getattr(agent_sc.scenario_type, "value", str(agent_sc.scenario_type))
    assert stype == BehaviorScenarioType.AGENT_COVERAGE.value
