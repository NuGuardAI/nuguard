"""Test the LangChain framework adapter on fixture Python source."""

from __future__ import annotations

import pytest
from pathlib import Path


from nuguard.models.sbom import EdgeRelationshipType, NodeType
try:
    from nuguard.sbom.extractor.framework_adapters.langchain import LangChainAdapter
except ImportError:
    pytest.skip("LangChainAdapter not yet ported to nuguard.sbom", allow_module_level=True)


@pytest.fixture
def adapter() -> LangChainAdapter:
    return LangChainAdapter()


# ---------------------------------------------------------------------------
# Fixture sources
# ---------------------------------------------------------------------------

LANGCHAIN_AGENT_SOURCE = """
from langchain.agents import AgentExecutor, create_react_agent
from langchain_openai import ChatOpenAI
from langchain_chroma import Chroma
from langchain.tools import tool

@tool
def search_docs(query: str) -> str:
    \"\"\"Search the document store.\"\"\"
    return "results"

llm = ChatOpenAI(model="gpt-4o")
vectorstore = Chroma()
agent = create_react_agent(llm, [search_docs], prompt=None)
executor = AgentExecutor(agent=agent, tools=[search_docs])
"""

LANGCHAIN_TOOL_SOURCE = """
from langchain.tools import StructuredTool, Tool

my_tool = Tool(name="calculator", func=lambda x: x, description="calc")
structured = StructuredTool.from_function(name="formatter", func=lambda x: x)
"""

LANGCHAIN_SQL_SOURCE = """
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent

db = SQLDatabase.from_uri("sqlite:///data.db")
agent = create_sql_agent(llm=None, db=db)
"""

EMPTY_SOURCE = ""

INVALID_SYNTAX = "def foo(:"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_agent_detected(adapter: LangChainAdapter) -> None:
    """AgentExecutor and create_react_agent are detected as AGENT nodes."""
    nodes, _ = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert len(agent_nodes) >= 1


def test_model_detected(adapter: LangChainAdapter) -> None:
    """ChatOpenAI is detected as a MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1
    assert any(n.name == "ChatOpenAI" for n in model_nodes)


def test_tool_decorator_detected(adapter: LangChainAdapter) -> None:
    """@tool decorated functions are detected as TOOL nodes."""
    nodes, _ = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert any(n.name == "search_docs" for n in tool_nodes)


def test_vector_store_detected(adapter: LangChainAdapter) -> None:
    """Chroma instantiation is detected as a DATASTORE (vector) node."""
    nodes, _ = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    ds_nodes = [n for n in nodes if n.component_type == NodeType.DATASTORE]
    assert any(n.name == "Chroma" for n in ds_nodes)


def test_tool_class_detected(adapter: LangChainAdapter) -> None:
    """Tool(name=...) and StructuredTool.from_function are detected."""
    nodes, _ = adapter.extract(Path("tools.py"), LANGCHAIN_TOOL_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    names = {n.name for n in tool_nodes}
    assert "calculator" in names
    assert "formatter" in names


def test_sql_datastore_detected(adapter: LangChainAdapter) -> None:
    """SQLDatabase is detected as a DATASTORE (relational) node."""
    nodes, _ = adapter.extract(Path("sql_agent.py"), LANGCHAIN_SQL_SOURCE)
    ds_nodes = [n for n in nodes if n.component_type == NodeType.DATASTORE]
    assert len(ds_nodes) >= 1


def test_edges_created(adapter: LangChainAdapter) -> None:
    """CALLS, USES, and ACCESSES edges are created between agent and components."""
    nodes, edges = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    rel_types = {e.relationship_type for e in edges}
    assert EdgeRelationshipType.CALLS in rel_types
    assert EdgeRelationshipType.USES in rel_types
    assert EdgeRelationshipType.ACCESSES in rel_types


def test_empty_source_returns_empty(adapter: LangChainAdapter) -> None:
    """An empty source file produces no nodes or edges."""
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: LangChainAdapter) -> None:
    """A file with a syntax error is silently skipped."""
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []


def test_node_confidence_in_range(adapter: LangChainAdapter) -> None:
    """All extracted nodes have confidence in [0, 1]."""
    nodes, _ = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    for node in nodes:
        assert 0.0 <= node.confidence <= 1.0


def test_node_ids_are_stable(adapter: LangChainAdapter) -> None:
    """Running the adapter twice on the same source produces the same IDs."""
    nodes1, _ = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    nodes2, _ = adapter.extract(Path("app.py"), LANGCHAIN_AGENT_SOURCE)
    ids1 = {n.id for n in nodes1}
    ids2 = {n.id for n in nodes2}
    assert ids1 == ids2
