"""Tests for nuguard.graph.serializer.render_graph_text."""
from __future__ import annotations

from nuguard.graph import build_attack_graph, render_graph_text
from nuguard.sbom.models import AiSbomDocument


def test_render_includes_target(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    text = render_graph_text(graph)
    assert "./graph-test-app" in text


def test_render_includes_node_slugs_and_names(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    text = render_graph_text(graph)
    assert "agent_1" in text
    assert "BankingAgent" in text
    assert "guardrail_1" in text
    assert "topic_filter" in text


def test_render_includes_system_prompt_and_guardrail_attributes(
    sbom_with_agent_guardrail_datastore: AiSbomDocument,
) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    text = render_graph_text(graph)
    assert "banking assistant" in text
    assert "hard_block" in text
    assert "investment_advice" in text


def test_render_includes_relationships(sbom_with_agent_guardrail_datastore: AiSbomDocument) -> None:
    graph = build_attack_graph(sbom_with_agent_guardrail_datastore)
    text = render_graph_text(graph)
    assert "agent_1 --calls--> tool_1" in text
    assert "agent_1 --accesses (read)--> datastore_1" in text
    assert "guardrail_1 --protects--> agent_1" in text


def test_render_empty_graph_has_no_relationships_placeholder() -> None:
    from nuguard.graph import AttackGraph

    graph = AttackGraph(target="./empty", nodes=[], edges=[])
    text = render_graph_text(graph)
    assert "(none)" in text
    assert "0 components, 0 relationships" in text


def test_long_string_attribute_is_truncated() -> None:
    from nuguard.graph.serializer import _format_value

    long_text = "x" * 1000
    formatted = _format_value(long_text)
    assert len(formatted) <= 401  # _MAX_STR_LEN + ellipsis
    assert formatted.endswith("…")
