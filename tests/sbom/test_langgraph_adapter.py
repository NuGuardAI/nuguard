"""Tests for the LangGraph framework adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.models.sbom import EdgeRelationshipType, NodeType
from nuguard.sbom.adapters.python.langgraph import LangGraphAdapter


@pytest.fixture
def adapter() -> LangGraphAdapter:
    return LangGraphAdapter()


LANGGRAPH_SOURCE = """
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import ToolNode
from langchain.tools import tool

@tool
def search(query: str) -> str:
    \"\"\"Search the web.\"\"\"
    return "results"

model = ChatOpenAI(model="gpt-4o")
graph = StateGraph(dict)
graph.add_node("agent", lambda s: s)
graph.add_node("tools", lambda s: s)
graph.add_edge("agent", "tools")
graph.add_edge("tools", END)
tools_node = ToolNode(tools=[search])
"""

CONDITIONAL_EDGES_SOURCE = """
from langgraph.graph import StateGraph

g = StateGraph(dict)
g.add_node("router", lambda s: s)
g.add_node("path_a", lambda s: s)
g.add_node("path_b", lambda s: s)
g.add_conditional_edges("router", lambda s: s["route"], {"a": "path_a", "b": "path_b"})
"""

PREBUILT_SOURCE = """
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

model = ChatOpenAI()
agent = create_react_agent(model, tools=[])
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_state_graph_detected(adapter: LangGraphAdapter) -> None:
    """StateGraph instantiation → AGENT node."""
    nodes, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    names = {n.name for n in agent_nodes}
    assert "StateGraph" in names or any("StateGraph" in n for n in names)


def test_add_node_detected(adapter: LangGraphAdapter) -> None:
    """add_node creates AGENT nodes for each named graph node."""
    nodes, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    names = {n.name for n in agent_nodes}
    assert "agent" in names or "tools" in names


def test_tool_node_detected(adapter: LangGraphAdapter) -> None:
    """ToolNode → TOOL node."""
    nodes, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert len(tool_nodes) >= 1


def test_tool_decorator_detected(adapter: LangGraphAdapter) -> None:
    """@tool decorated function → TOOL node."""
    nodes, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert any(n.name == "search" for n in tool_nodes)


def test_model_detected(adapter: LangGraphAdapter) -> None:
    """ChatOpenAI → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1
    assert any("ChatOpenAI" in n.name for n in model_nodes)


def test_edges_created(adapter: LangGraphAdapter) -> None:
    """CALLS and USES edges are created."""
    nodes, edges = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    rel_types = {e.relationship_type for e in edges}
    assert EdgeRelationshipType.CALLS in rel_types or EdgeRelationshipType.USES in rel_types


def test_conditional_edges_produce_relationship_hints(adapter: LangGraphAdapter) -> None:
    """add_conditional_edges produces relationship hints."""
    nodes, edges = adapter.extract(Path("app.py"), CONDITIONAL_EDGES_SOURCE)
    # Should have CALLS edges from router → path_a / path_b
    calls_edges = [e for e in edges if e.relationship_type == EdgeRelationshipType.CALLS]
    assert len(calls_edges) >= 1


def test_prebuilt_agent_detected(adapter: LangGraphAdapter) -> None:
    """create_react_agent → AGENT node."""
    nodes, _ = adapter.extract(Path("app.py"), PREBUILT_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert len(agent_nodes) >= 1


def test_empty_source_returns_empty(adapter: LangGraphAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: LangGraphAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []


def test_node_ids_are_stable(adapter: LangGraphAdapter) -> None:
    nodes1, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    nodes2, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    ids1 = {n.id for n in nodes1}
    ids2 = {n.id for n in nodes2}
    assert ids1 == ids2


def test_confidence_in_range(adapter: LangGraphAdapter) -> None:
    nodes, _ = adapter.extract(Path("app.py"), LANGGRAPH_SOURCE)
    for node in nodes:
        assert 0.0 <= node.confidence <= 1.0
