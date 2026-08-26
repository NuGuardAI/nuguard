"""Tests for escalation-phase ordering in ScenarioGenerator (Fix 1/2).

Covers:
- attack_phase_for() maps ScenarioType values to the documented 1-9 phases.
- generate() persists attack_phase onto each scenario and sorts ascending.
- generate_from_catalog() (previously impact-score-only, bypassing phase
  discipline entirely) now also sorts by (attack_phase, -impact_score).
"""
from __future__ import annotations

import uuid

from nuguard.models.exploit_chain import ScenarioType
from nuguard.redteam.scenarios.generator import ScenarioGenerator, attack_phase_for
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, name)


def _agent(name: str, system_prompt: str = "") -> Node:
    return Node(
        id=_uuid(name),
        name=name,
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(system_prompt_excerpt=system_prompt),
    )


def _tool(name: str, description: str = "") -> Node:
    return Node(
        id=_uuid(name),
        name=name,
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(description=description),
    )


def _datastore(name: str, pii_fields: list[str] | None = None) -> Node:
    return Node(
        id=_uuid(name),
        name=name,
        component_type=ComponentType.DATASTORE,
        confidence=0.9,
        metadata=NodeMetadata(pii_fields=pii_fields or []),
    )


def _edge(src: Node, tgt: Node) -> Edge:
    return Edge(source=src.id, target=tgt.id, relationship_type=RelationshipType.CALLS)


def _rich_sbom() -> AiSbomDocument:
    agent = _agent("fintech-assistant", "I help with banking and payments")
    tools = [
        _tool("web-browser", "fetch and browse web URLs via HTTP"),
        _tool("send-email", "send email to customers via smtp"),
        _tool("transfer-funds", "transfer funds between accounts"),
    ]
    ds = _datastore("user-db", pii_fields=["name", "email", "phone", "address"])
    edges = [_edge(agent, t) for t in tools]
    return AiSbomDocument(
        target="test-app", nodes=[agent, *tools, ds], edges=edges, summary=ScanSummary(),
    )


def test_attack_phase_for_recon_is_phase_1() -> None:
    assert attack_phase_for(ScenarioType.REFUSAL_ORACLE.value) == 1
    assert attack_phase_for(ScenarioType.BOUNDARY_SELF_PROBE.value) == 1


def test_attack_phase_for_destructive_is_phase_9() -> None:
    assert attack_phase_for(ScenarioType.UNAUTHORIZED_TRANSACTION.value) == 9
    assert attack_phase_for(ScenarioType.DESTRUCTIVE_RECORD_MUTATION.value) == 9


def test_attack_phase_for_unknown_defaults_to_5() -> None:
    assert attack_phase_for("__not_a_real_scenario_type__") == 5


def test_generate_persists_attack_phase_and_sorts_ascending() -> None:
    sbom = _rich_sbom()
    generator = ScenarioGenerator(sbom)
    scenarios = generator.generate(with_guided=False)
    assert len(scenarios) > 0

    phases = [s.attack_phase for s in scenarios]
    assert phases == sorted(phases), "scenarios must be sorted by ascending attack_phase"
    for s in scenarios:
        assert s.attack_phase == attack_phase_for(s.scenario_type.value)


def test_generate_from_catalog_sorts_by_phase_then_impact() -> None:
    sbom = _rich_sbom()
    generator = ScenarioGenerator(sbom)
    scenarios = generator.generate_from_catalog(scan_profile="full", with_guided=False)
    assert len(scenarios) > 0

    phases = [s.attack_phase for s in scenarios]
    assert phases == sorted(phases), (
        "catalog-sourced scenarios must carry real attack_phase ordering, "
        "not just impact-score-descending"
    )
    for s in scenarios:
        assert s.attack_phase == attack_phase_for(s.scenario_type.value)

    # Within any single phase, impact_score must still be descending.
    from itertools import groupby
    for _phase, group in groupby(scenarios, key=lambda s: s.attack_phase):
        impacts = [g.impact_score for g in group]
        assert impacts == sorted(impacts, reverse=True)
