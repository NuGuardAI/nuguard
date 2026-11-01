"""Tests for per-agent tool tiers (redteam-proposal.md W4/W10, Phase 2b)."""
from __future__ import annotations

import uuid

from nuguard.redteam.catalog.capability import CapabilityDetector
from nuguard.redteam.catalog.taxonomy import Capability as C
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, name)


def _agent(name: str, system_prompt: str = "") -> Node:
    return Node(
        id=_uuid(name), name=name, component_type=ComponentType.AGENT,
        confidence=0.9, metadata=NodeMetadata(system_prompt_excerpt=system_prompt),
    )


def _tool(name: str, description: str = "") -> Node:
    return Node(
        id=_uuid(name), name=name, component_type=ComponentType.TOOL,
        confidence=0.9, metadata=NodeMetadata(description=description),
    )


def _calls(src: Node, tgt: Node) -> Edge:
    return Edge(source=src.id, target=tgt.id, relationship_type=RelationshipType.CALLS)


def test_tiers_classify_read_write_admin_per_agent() -> None:
    support_agent = _agent("support_agent")
    admin_agent = _agent("admin_agent")
    get_balance = _tool("get_balance", "Retrieve the customer's account balance")
    transfer_funds = _tool("transfer_funds", "Transfer money between accounts")
    grant_admin_role = _tool("grant_admin_role", "Grant admin role to a user")

    sbom = AiSbomDocument(
        target="unit-test",
        nodes=[support_agent, admin_agent, get_balance, transfer_funds, grant_admin_role],
        edges=[
            _calls(support_agent, get_balance),
            _calls(admin_agent, transfer_funds),
            _calls(admin_agent, grant_admin_role),
        ],
    )
    profile = CapabilityDetector(sbom).build()
    tiers = {t.agent_id: t for t in profile.agent_tool_tiers}

    support_tier = tiers[str(support_agent.id)]
    assert support_tier.read_tool_ids == {str(get_balance.id)}
    assert support_tier.write_tool_ids == frozenset()
    assert support_tier.admin_tool_ids == frozenset()

    admin_tier = tiers[str(admin_agent.id)]
    assert str(transfer_funds.id) in admin_tier.write_tool_ids
    assert str(grant_admin_role.id) in admin_tier.write_tool_ids  # admin implies write
    assert admin_tier.admin_tool_ids == {str(grant_admin_role.id)}

    assert C.MULTI_TIER_AGENTS in profile.capabilities


def test_single_agent_never_sets_multi_tier_agents() -> None:
    agent = _agent("solo_agent")
    tool = _tool("grant_admin_role", "Grant admin role to a user")
    sbom = AiSbomDocument(target="unit-test", nodes=[agent, tool], edges=[_calls(agent, tool)])
    profile = CapabilityDetector(sbom).build()

    assert len(profile.agent_tool_tiers) == 1
    assert C.MULTI_TIER_AGENTS not in profile.capabilities


def test_identical_privilege_sets_do_not_set_multi_tier_agents() -> None:
    agent_a = _agent("agent_a")
    agent_b = _agent("agent_b")
    shared_tool = _tool("transfer_funds", "Transfer money between accounts")
    sbom = AiSbomDocument(
        target="unit-test", nodes=[agent_a, agent_b, shared_tool],
        edges=[_calls(agent_a, shared_tool), _calls(agent_b, shared_tool)],
    )
    profile = CapabilityDetector(sbom).build()

    assert C.MULTI_TIER_AGENTS not in profile.capabilities


def test_two_read_only_agents_do_not_set_multi_tier_agents() -> None:
    agent_a = _agent("agent_a")
    agent_b = _agent("agent_b")
    read_tool_a = _tool("get_balance", "Retrieve balance")
    read_tool_b = _tool("get_profile", "Retrieve profile")
    sbom = AiSbomDocument(
        target="unit-test", nodes=[agent_a, agent_b, read_tool_a, read_tool_b],
        edges=[_calls(agent_a, read_tool_a), _calls(agent_b, read_tool_b)],
    )
    profile = CapabilityDetector(sbom).build()

    assert C.MULTI_TIER_AGENTS not in profile.capabilities


def test_intent_keywords_extracted_from_agent_text() -> None:
    agent = _agent("triage_agent", system_prompt="Route fraud detection and compliance requests")
    sbom = AiSbomDocument(target="unit-test", nodes=[agent], edges=[])
    profile = CapabilityDetector(sbom).build()

    [tier] = profile.agent_tool_tiers
    assert "fraud" in tier.intent_keywords
    assert "compliance" in tier.intent_keywords
    # Short/stopword tokens must not appear.
    assert "and" not in tier.intent_keywords
