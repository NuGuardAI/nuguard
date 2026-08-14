"""Tests for gap-driven capability discovery — probing the live agent for
tools, sub-agents, and its system prompt when the AI-SBOM is missing them.

Covers ``nuguard.common.discovery.sbom_capability_gaps``,
``run_capability_discovery``, and ``apply_capability_discovery``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from nuguard.common.discovery import (
    AgentCapabilityGap,
    CapabilityDiscoveryResult,
    apply_capability_discovery,
    run_capability_discovery,
    sbom_capability_gaps,
)
from nuguard.sbom.models import AiSbomDocument, Edge, Node, SourceLocation
from nuguard.sbom.types import ComponentType, RelationshipType


def _make_client(responses: dict[str, str]) -> MagicMock:
    """A client whose .send() returns *responses* keyed by which probe matched."""
    client = MagicMock()

    async def _send(message: str, session=None):  # noqa: ANN001
        if "tools, functions" in message:
            return responses.get("tools", ""), {}
        if "hand off or delegate" in message:
            return responses.get("subagents", ""), {}
        if "instructions or system prompt" in message:
            return responses.get("system_prompt", ""), {}
        return "", {}

    client.send = AsyncMock(side_effect=_send)
    return client


def _make_session() -> MagicMock:
    session = MagicMock()
    session.session_id = "capability-discovery-test"
    return session


def _agent_node(name: str = "Support Agent") -> Node:
    return Node(name=name, component_type=ComponentType.AGENT, confidence=0.9)


def _tool_node(name: str) -> Node:
    return Node(name=name, component_type=ComponentType.TOOL, confidence=0.9)


# ---------------------------------------------------------------------------
# sbom_capability_gaps
# ---------------------------------------------------------------------------


def test_no_sbom_returns_no_gaps():
    assert sbom_capability_gaps(None) == []


def test_fully_populated_agent_has_no_gap():
    agent = _agent_node()
    agent.metadata.system_prompt_excerpt = "You are a helpful assistant."
    tool = _tool_node("BookFlight")
    subagent = _agent_node("Billing Agent")
    subagent.metadata.system_prompt_excerpt = "You handle billing questions."
    doc = AiSbomDocument(
        target="test-target",
        nodes=[agent, tool, subagent],
        edges=[
            Edge(source=agent.id, target=tool.id, relationship_type=RelationshipType.CALLS),
            Edge(source=agent.id, target=subagent.id, relationship_type=RelationshipType.DELEGATES_TO),
            # The sub-agent is itself an AGENT node — give it a CALLS/DELEGATES_TO
            # edge too so it doesn't register its own gap in this assertion.
            Edge(source=subagent.id, target=tool.id, relationship_type=RelationshipType.CALLS),
            Edge(source=subagent.id, target=agent.id, relationship_type=RelationshipType.DELEGATES_TO),
        ],
    )
    assert sbom_capability_gaps(doc) == []


def test_agent_missing_all_three_is_flagged():
    agent = _agent_node()
    doc = AiSbomDocument(target="test-target", nodes=[agent], edges=[])
    gaps = sbom_capability_gaps(doc)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.agent_id == str(agent.id)
    assert gap.needs_system_prompt is True
    assert gap.needs_tools is True
    assert gap.needs_subagents is True
    assert gap.has_gap is True


def test_agent_with_tools_only_missing_prompt_and_subagents():
    agent = _agent_node()
    tool = _tool_node("BookFlight")
    doc = AiSbomDocument(
        target="test-target",
        nodes=[agent, tool],
        edges=[Edge(source=agent.id, target=tool.id, relationship_type=RelationshipType.CALLS)],
    )
    gaps = sbom_capability_gaps(doc)
    assert len(gaps) == 1
    assert gaps[0].needs_tools is False
    assert gaps[0].needs_system_prompt is True
    assert gaps[0].needs_subagents is True


def test_non_agent_nodes_are_ignored():
    doc = AiSbomDocument(target="test-target", nodes=[_tool_node("StandaloneTool")], edges=[])
    assert sbom_capability_gaps(doc) == []


# ---------------------------------------------------------------------------
# run_capability_discovery
# ---------------------------------------------------------------------------


async def test_no_gaps_sends_no_probes():
    client = _make_client({})
    result = await run_capability_discovery(client, _make_session(), [])
    assert result.probes_sent == 0
    assert result.raw_responses == {}
    client.send.assert_not_called()


async def test_sends_only_probes_needed_to_cover_gaps():
    gap = AgentCapabilityGap(
        agent_id="a1", agent_name="Support Agent",
        needs_system_prompt=False, needs_tools=True, needs_subagents=False,
    )
    client = _make_client({"tools": "- BookFlight\n- CancelBooking"})
    result = await run_capability_discovery(client, _make_session(), [gap])
    assert client.send.call_count == 1
    assert result.probes_sent == 1
    assert "tools" in result.raw_responses
    assert "subagents" not in result.raw_responses


async def test_refusal_is_recorded_but_not_an_error():
    gap = AgentCapabilityGap(
        agent_id="a1", agent_name="Support Agent",
        needs_system_prompt=True, needs_tools=False, needs_subagents=False,
    )
    client = _make_client({"system_prompt": "Sorry, I cannot share that information."})
    result = await run_capability_discovery(client, _make_session(), [gap])
    assert result.probes_sent == 1
    assert "system_prompt" not in result.raw_responses


async def test_send_exception_is_non_fatal():
    gap = AgentCapabilityGap(
        agent_id="a1", agent_name="Support Agent",
        needs_system_prompt=False, needs_tools=True, needs_subagents=False,
    )
    client = MagicMock()
    client.send = AsyncMock(side_effect=RuntimeError("boom"))
    result = await run_capability_discovery(client, _make_session(), [gap])
    assert result.probes_sent == 0
    assert result.raw_responses == {}


# ---------------------------------------------------------------------------
# apply_capability_discovery
# ---------------------------------------------------------------------------


def test_apply_fills_system_prompt_excerpt():
    agent = _agent_node()
    doc = AiSbomDocument(target="test-target", nodes=[agent], edges=[])
    gaps = sbom_capability_gaps(doc)
    result = CapabilityDiscoveryResult(
        raw_responses={
            "system_prompt": (
                "You are a customer support assistant for Acme Airlines. "
                "Always verify the customer's identity before sharing booking details."
            )
        },
        probes_sent=1,
    )
    notes = apply_capability_discovery(doc, gaps, result)
    assert agent.metadata.system_prompt_excerpt
    assert "Acme Airlines" in agent.metadata.system_prompt_excerpt
    assert any(e.kind == "dynamic_probe" for e in agent.evidence)
    assert any("system_prompt_excerpt" in n for n in notes)


def test_apply_adds_new_tool_node_and_calls_edge():
    agent = _agent_node()
    doc = AiSbomDocument(target="test-target", nodes=[agent], edges=[])
    gaps = sbom_capability_gaps(doc)
    result = CapabilityDiscoveryResult(
        raw_responses={"tools": "- Book a flight\n- Cancel a reservation\n- Check in for a flight"},
        probes_sent=1,
    )
    apply_capability_discovery(doc, gaps, result)
    tool_names = {n.name for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "Book a flight" in tool_names
    calls_edges = [e for e in doc.edges if e.relationship_type == RelationshipType.CALLS]
    assert len(calls_edges) == 3
    assert all(e.source == agent.id for e in calls_edges)
    new_tool = next(n for n in doc.nodes if n.name == "Book a flight")
    assert new_tool.confidence == 0.5
    assert new_tool.evidence[0].kind == "dynamic_probe"


def test_apply_does_not_duplicate_existing_tool():
    agent = _agent_node()
    existing_tool = _tool_node("Book a flight")
    doc = AiSbomDocument(
        target="test-target",
        nodes=[agent, existing_tool],
        edges=[Edge(source=agent.id, target=existing_tool.id, relationship_type=RelationshipType.CALLS)],
    )
    # The agent already has a CALLS edge, so needs_tools is False even though
    # it still has an (unrelated) sub-agent gap — a tools-probe response must
    # not add a duplicate "Book a flight" tool node.
    gaps = sbom_capability_gaps(doc)
    assert gaps[0].needs_tools is False
    result = CapabilityDiscoveryResult(raw_responses={"tools": "- Book a flight"}, probes_sent=1)
    notes = apply_capability_discovery(doc, gaps, result)
    assert notes == []
    assert len([n for n in doc.nodes if n.component_type == ComponentType.TOOL]) == 1


def test_apply_adds_subagent_node_and_delegates_to_edge():
    agent = _agent_node()
    doc = AiSbomDocument(target="test-target", nodes=[agent], edges=[])
    gaps = sbom_capability_gaps(doc)
    result = CapabilityDiscoveryResult(
        raw_responses={"subagents": "- Billing Agent\n- Fraud Review Agent"},
        probes_sent=1,
    )
    apply_capability_discovery(doc, gaps, result)
    subagent_names = {
        n.name for n in doc.nodes
        if n.component_type == ComponentType.AGENT and n.id != agent.id
    }
    assert subagent_names == {"Billing Agent", "Fraud Review Agent"}
    delegates_edges = [e for e in doc.edges if e.relationship_type == RelationshipType.DELEGATES_TO]
    assert len(delegates_edges) == 2


def test_apply_with_empty_raw_responses_is_noop():
    agent = _agent_node()
    doc = AiSbomDocument(target="test-target", nodes=[agent], edges=[])
    gaps = sbom_capability_gaps(doc)
    notes = apply_capability_discovery(doc, gaps, CapabilityDiscoveryResult())
    assert notes == []
    assert agent.metadata.system_prompt_excerpt is None
    assert len(doc.nodes) == 1


def test_apply_never_overwrites_existing_system_prompt():
    agent = _agent_node()
    agent.metadata.system_prompt_excerpt = "Original excerpt already present."
    tool = _tool_node("BookFlight")
    doc = AiSbomDocument(
        target="test-target",
        nodes=[agent, tool],
        edges=[Edge(source=agent.id, target=tool.id, relationship_type=RelationshipType.CALLS)],
    )
    # Agent still has a sub-agent gap, so it appears in gaps, but
    # needs_system_prompt is False — a system_prompt probe response must not
    # touch the existing excerpt.
    gaps = sbom_capability_gaps(doc)
    assert gaps[0].needs_system_prompt is False
    result = CapabilityDiscoveryResult(
        raw_responses={
            "system_prompt": "Some other instructions that should never be applied here.",
            "subagents": "- Billing Agent",
        },
        probes_sent=2,
    )
    apply_capability_discovery(doc, gaps, result)
    assert agent.metadata.system_prompt_excerpt == "Original excerpt already present."


def test_evidence_location_is_runtime_marker():
    agent = _agent_node()
    doc = AiSbomDocument(target="test-target", nodes=[agent], edges=[])
    gaps = sbom_capability_gaps(doc)
    result = CapabilityDiscoveryResult(
        raw_responses={"tools": "- Book a flight"}, probes_sent=1,
    )
    apply_capability_discovery(doc, gaps, result)
    new_tool = next(n for n in doc.nodes if n.name == "Book a flight")
    loc = new_tool.evidence[0].location
    assert isinstance(loc, SourceLocation)
    assert loc.path == "<runtime>"
