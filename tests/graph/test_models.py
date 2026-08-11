"""Tests for nuguard.graph.models.AttackGraph traversal."""
from __future__ import annotations

from nuguard.graph import build_attack_graph
from nuguard.sbom.models import AiSbomDocument


def test_get_node_returns_none_for_unknown_slug(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    assert graph.get_node("agent_99") is None


def test_nodes_by_type(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    tools = graph.nodes_by_type("TOOL")
    assert [n.id for n in tools] == ["tool_1"]
    assert graph.nodes_by_type("NONEXISTENT_TYPE") == []


def test_outgoing_and_incoming(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    out_edges = graph.outgoing("agent_1")
    assert {e.target for e in out_edges} == {"tool_1", "datastore_1"}

    in_edges = graph.incoming("agent_1")
    assert {e.source for e in in_edges} == {"guardrail_1"}


def test_neighbors_out_direction(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    neighbors = graph.neighbors("agent_1", direction="out")
    assert {n.id for n in neighbors} == {"tool_1", "datastore_1"}


def test_neighbors_in_direction(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    neighbors = graph.neighbors("agent_1", direction="in")
    assert {n.id for n in neighbors} == {"guardrail_1"}


def test_neighbors_both_direction(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    neighbors = graph.neighbors("agent_1", direction="both")
    assert {n.id for n in neighbors} == {"tool_1", "datastore_1", "guardrail_1"}


def test_reachable_from(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    reachable = graph.reachable_from("guardrail_1")
    # guardrail -> agent -> {tool, datastore}
    assert {n.id for n in reachable} == {"agent_1", "tool_1", "datastore_1"}


def test_reachable_from_respects_max_depth(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    reachable = graph.reachable_from("guardrail_1", max_depth=1)
    assert {n.id for n in reachable} == {"agent_1"}


def test_reachable_from_unknown_node_returns_empty(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    assert graph.reachable_from("agent_99") == []


def test_shortest_path(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    path = graph.shortest_path("guardrail_1", "tool_1")
    assert path is not None
    assert [n.id for n in path] == ["guardrail_1", "agent_1", "tool_1"]


def test_shortest_path_same_node(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    path = graph.shortest_path("agent_1", "agent_1")
    assert path is not None
    assert [n.id for n in path] == ["agent_1"]


def test_shortest_path_unreachable_returns_none(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    # tool_1 has no outgoing edges, so nothing is reachable from it.
    assert graph.shortest_path("tool_1", "guardrail_1") is None


def test_entry_agents_excludes_agents_called_by_other_agents(
    sbom_with_agent_guardrail_datastore: AiSbomDocument,
) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    # Single agent, only called (via CALLS) by itself toward a tool — it's an entry agent.
    assert [n.id for n in graph.entry_agents()] == ["agent_1"]
