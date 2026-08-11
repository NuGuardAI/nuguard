"""Tests for nuguard.graph.builder.build_attack_graph."""
from __future__ import annotations

from nuguard.graph import build_attack_graph
from nuguard.sbom.models import AiSbomDocument


def test_node_count_matches_sbom(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    assert len(graph.nodes) == 4


def test_dangling_edge_is_dropped(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    # 4 edges defined, 1 dangling (unresolved target) — only 3 should survive.
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    assert len(graph.edges) == 3


def test_slugs_are_short_and_typed(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    slugs = {n.id for n in graph.nodes}
    assert slugs == {"agent_1", "tool_1", "guardrail_1", "datastore_1"}


def test_sbom_id_preserved_for_cross_referencing(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    agent = graph.get_node("agent_1")
    assert agent is not None
    original_ids = {str(n.id) for n in sbom_with_agent_guardrail_datastore.nodes}
    assert agent.sbom_id in original_ids


def test_system_prompt_excerpt_carried_on_agent(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    agent = graph.get_node("agent_1")
    assert agent is not None
    assert "banking assistant" in agent.attributes["system_prompt_excerpt"]
    assert agent.attributes["injection_risk_score"] == 0.72


def test_guardrail_config_carried(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    guardrail = graph.get_node("guardrail_1")
    assert guardrail is not None
    assert guardrail.attributes["refusal_style"] == "hard_block"
    assert guardrail.attributes["blocked_topics"] == ["investment_advice", "tax_advice"]
    assert "financial-advice" in guardrail.attributes["rules_excerpt"]


def test_pii_fields_carried_on_datastore(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    datastore = graph.get_node("datastore_1")
    assert datastore is not None
    assert datastore.attributes["pii_fields"] == ["name", "ssn", "address"]


def test_default_only_fields_are_excluded(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    """Fields left at their pydantic default (e.g. chat_payload_list=False) shouldn't bloat every node."""
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    tool = graph.get_node("tool_1")
    assert tool is not None
    assert "chat_payload_list" not in tool.attributes
    assert "extras" not in tool.attributes


def test_access_type_carried_on_edge(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    accesses = [e for e in graph.edges if e.relationship_type == "ACCESSES"]
    assert len(accesses) == 1
    assert accesses[0].access_type == "read"
    assert accesses[0].source == "agent_1"
    assert accesses[0].target == "datastore_1"


def test_multiple_same_type_nodes_get_incrementing_slugs() -> None:
    import uuid
    from datetime import datetime, timezone

    from nuguard.sbom.models import Node, NodeMetadata, NodeType

    ids = [uuid.uuid4() for _ in range(3)]
    doc = AiSbomDocument(
        generated_at=datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc),
        generator="nuguard-test",
        target="./x",
        nodes=[
            Node(id=i, name=f"tool_{n}", component_type=NodeType.TOOL, confidence=0.9, metadata=NodeMetadata())
            for n, i in enumerate(ids)
        ],
    )
    graph = build_attack_graph(doc)
    assert {n.id for n in graph.nodes} == {"tool_1", "tool_2", "tool_3"}
