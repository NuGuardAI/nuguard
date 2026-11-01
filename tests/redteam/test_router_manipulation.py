"""Tests for the W4 router-manipulation builders (catalog Q-series)."""
from __future__ import annotations

import uuid

from nuguard.redteam.catalog.capability import CapabilityDetector
from nuguard.redteam.catalog.selector import select_scenarios
from nuguard.redteam.catalog.taxonomy import Capability as C
from nuguard.redteam.scenarios.router_manipulation import (
    _most_privileged_tier,
    _tool_names,
    build_router_keyword_seeding,
    build_router_privileged_probe,
)
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.UUID("00000000-0000-0000-0000-000000000006")


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


def _two_tier_sbom() -> tuple[AiSbomDocument, Node, Node]:
    triage = _agent("triage_agent", system_prompt="Route customer requests to specialists")
    privileged = _agent("fraud_ops_agent", system_prompt="Handle fraud detection and compliance cases")
    get_balance = _tool("get_balance", "Retrieve the customer's account balance")
    grant_admin_role = _tool("grant_admin_role", "Grant admin role to a user")
    sbom = AiSbomDocument(
        target="unit-test",
        nodes=[triage, privileged, get_balance, grant_admin_role],
        edges=[_calls(triage, get_balance), _calls(privileged, grant_admin_role)],
    )
    return sbom, triage, privileged


# ── _most_privileged_tier / _tool_names ──────────────────────────────────────


def test_most_privileged_tier_picks_the_agent_with_admin_tools() -> None:
    sbom, triage, privileged = _two_tier_sbom()
    profile = CapabilityDetector(sbom).build()
    tier = _most_privileged_tier(profile.agent_tool_tiers)
    assert tier is not None
    assert tier.agent_id == str(privileged.id)


def test_most_privileged_tier_handles_empty_tiers() -> None:
    assert _most_privileged_tier(()) is None


def test_tool_names_resolves_ids_to_names() -> None:
    sbom, _triage, privileged = _two_tier_sbom()
    profile = CapabilityDetector(sbom).build()
    tier = _most_privileged_tier(profile.agent_tool_tiers)
    assert tier is not None
    names = _tool_names(sbom, tier.admin_tool_ids)
    assert names == ["grant_admin_role"]


# ── build_router_keyword_seeding / build_router_privileged_probe ────────────


def test_keyword_seeding_returns_none_without_keywords() -> None:
    sbom, triage, privileged = _two_tier_sbom()
    profile = CapabilityDetector(sbom).build()
    tier = _most_privileged_tier(profile.agent_tool_tiers)
    assert tier is not None
    empty_tier = tier.__class__(
        agent_id=tier.agent_id, read_tool_ids=tier.read_tool_ids,
        write_tool_ids=tier.write_tool_ids, admin_tool_ids=tier.admin_tool_ids,
        intent_keywords=frozenset(),
    )
    assert build_router_keyword_seeding(str(triage.id), "triage", empty_tier, sbom) is None


def test_keyword_seeding_seeds_payload_with_privileged_intent_keywords() -> None:
    sbom, triage, privileged = _two_tier_sbom()
    profile = CapabilityDetector(sbom).build()
    tier = _most_privileged_tier(profile.agent_tool_tiers)
    assert tier is not None

    scenario = build_router_keyword_seeding(str(triage.id), "triage_agent", tier, sbom)

    assert scenario is not None
    assert scenario.chain is not None
    payload = scenario.chain.steps[0].payload.lower()
    assert any(kw in payload for kw in tier.intent_keywords)
    assert scenario.chain.steps[0].success_signal == "grant_admin_role"


def test_privileged_probe_returns_none_without_read_tools() -> None:
    sbom, triage, privileged = _two_tier_sbom()
    profile = CapabilityDetector(sbom).build()
    tier = _most_privileged_tier(profile.agent_tool_tiers)
    assert tier is not None
    # This fixture's privileged agent has only an admin tool, no read tool.
    assert build_router_privileged_probe(str(triage.id), "triage", tier, sbom) is None


def test_privileged_probe_targets_the_privileged_agents_read_tool() -> None:
    triage = _agent("triage_agent")
    privileged = _agent("fraud_ops_agent", system_prompt="fraud detection compliance")
    read_tool = _tool("get_case_file", "Retrieve a fraud case file")
    admin_tool = _tool("waive_aml_check", "Waive AML check for a transaction")
    sbom = AiSbomDocument(
        target="unit-test", nodes=[triage, privileged, read_tool, admin_tool],
        edges=[_calls(privileged, read_tool), _calls(privileged, admin_tool)],
    )
    profile = CapabilityDetector(sbom).build()
    tier = _most_privileged_tier(profile.agent_tool_tiers)
    assert tier is not None

    scenario = build_router_privileged_probe(str(triage.id), "triage_agent", tier, sbom)

    assert scenario is not None
    assert scenario.chain is not None
    assert "get_case_file" in scenario.chain.steps[0].payload
    assert scenario.chain.steps[0].success_signal == "get_case_file"


# ── End-to-end: Q01/Q02 generated from select_scenarios ─────────────────────


def test_q01_and_q02_generated_end_to_end() -> None:
    triage = _agent("triage_agent", system_prompt="Route customer requests to specialists")
    privileged = _agent("fraud_ops_agent", system_prompt="fraud detection compliance review")
    read_tool = _tool("get_case_file", "Retrieve a fraud case file")
    admin_tool = _tool("grant_admin_role", "Grant admin role to a user")
    sbom = AiSbomDocument(
        target="unit-test", nodes=[triage, privileged, read_tool, admin_tool],
        edges=[_calls(privileged, read_tool), _calls(privileged, admin_tool)],
    )
    profile = CapabilityDetector(sbom).build()
    assert C.MULTI_TIER_AGENTS in profile.capabilities

    scenarios, _coverage = select_scenarios(sbom, profile, scan_profile="full")

    assert [s for s in scenarios if s.catalog_id == "Q01"]
    assert [s for s in scenarios if s.catalog_id == "Q02"]


def test_router_scenarios_skipped_when_entry_agent_is_the_privileged_one() -> None:
    solo = _agent("solo_agent", system_prompt="admin operations")
    admin_tool = _tool("grant_admin_role", "Grant admin role to a user")
    sbom = AiSbomDocument(
        target="unit-test", nodes=[solo, admin_tool], edges=[_calls(solo, admin_tool)],
    )
    profile = CapabilityDetector(sbom).build()
    # Single agent: MULTI_TIER_AGENTS never sets (needs >=2 agents) — Q01/Q02
    # are capability-gated out before the entry==privileged check even runs.
    assert C.MULTI_TIER_AGENTS not in profile.capabilities

    scenarios, _coverage = select_scenarios(sbom, profile, scan_profile="full")

    assert not [s for s in scenarios if s.catalog_id in ("Q01", "Q02")]
