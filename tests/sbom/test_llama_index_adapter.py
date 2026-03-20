"""Tests for the LlamaIndex adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.models.sbom import DatastoreType, EdgeRelationshipType, NodeType
from nuguard.sbom.adapters.python.llamaindex import LlamaIndexAdapter


@pytest.fixture
def adapter() -> LlamaIndexAdapter:
    return LlamaIndexAdapter()


LLAMA_INDEX_SOURCE = """
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
from llama_index.core.agent import ReActAgent
from llama_index.core.llms import OpenAI
from llama_index.core.tools import FunctionTool, QueryEngineTool

def multiply(a: float, b: float) -> float:
    return a * b

multiply_tool = FunctionTool.from_defaults(fn=multiply, name="multiply")
reader = SimpleDirectoryReader("./data")
documents = reader.load_data()
index = VectorStoreIndex.from_documents(documents)
llm = OpenAI(model="gpt-4o")
agent = ReActAgent.from_tools([multiply_tool], llm=llm, verbose=True)
"""

KNOWLEDGE_GRAPH_SOURCE = """
from llama_index.core import KnowledgeGraphIndex
from llama_index.core.llms import Anthropic

llm = Anthropic(model="claude-3-5-sonnet-20241022")
kg_index = KnowledgeGraphIndex.from_documents(documents, llm=llm)
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_vector_store_index_detected(adapter: LlamaIndexAdapter) -> None:
    """VectorStoreIndex → DATASTORE (vector) node."""
    nodes, _ = adapter.extract(Path("app.py"), LLAMA_INDEX_SOURCE)
    ds_nodes = [n for n in nodes if n.component_type == NodeType.DATASTORE]
    assert len(ds_nodes) >= 1
    vector_nodes = [n for n in ds_nodes if n.metadata.datastore_type == DatastoreType.VECTOR]
    assert len(vector_nodes) >= 1


def test_react_agent_detected(adapter: LlamaIndexAdapter) -> None:
    """ReActAgent → AGENT node."""
    nodes, _ = adapter.extract(Path("app.py"), LLAMA_INDEX_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert len(agent_nodes) >= 1


def test_openai_llm_detected(adapter: LlamaIndexAdapter) -> None:
    """OpenAI(model=...) → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), LLAMA_INDEX_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1


def test_function_tool_detected(adapter: LlamaIndexAdapter) -> None:
    """FunctionTool → TOOL node."""
    nodes, _ = adapter.extract(Path("app.py"), LLAMA_INDEX_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert len(tool_nodes) >= 1


def test_reader_detected_as_knowledge_base(adapter: LlamaIndexAdapter) -> None:
    """SimpleDirectoryReader → DATASTORE (knowledge_base) node."""
    nodes, _ = adapter.extract(Path("app.py"), LLAMA_INDEX_SOURCE)
    ds_nodes = [n for n in nodes if n.component_type == NodeType.DATASTORE]
    kb_nodes = [n for n in ds_nodes if n.metadata.datastore_type == DatastoreType.KNOWLEDGE_BASE]
    assert len(kb_nodes) >= 1


def test_knowledge_graph_index_detected(adapter: LlamaIndexAdapter) -> None:
    """KnowledgeGraphIndex → DATASTORE (knowledge_base) node."""
    nodes, _ = adapter.extract(Path("kg.py"), KNOWLEDGE_GRAPH_SOURCE)
    ds_nodes = [n for n in nodes if n.component_type == NodeType.DATASTORE]
    assert len(ds_nodes) >= 1


def test_edges_created(adapter: LlamaIndexAdapter) -> None:
    """USES, ACCESSES, and CALLS edges are created."""
    nodes, edges = adapter.extract(Path("app.py"), LLAMA_INDEX_SOURCE)
    rel_types = {e.relationship_type for e in edges}
    assert len(rel_types) >= 1


def test_empty_source_returns_empty(adapter: LlamaIndexAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: LlamaIndexAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []
