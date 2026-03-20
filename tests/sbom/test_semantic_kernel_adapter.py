"""Tests for the Semantic Kernel adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.models.sbom import EdgeRelationshipType, NodeType
from nuguard.sbom.adapters.python.semantic_kernel import SemanticKernelAdapter


@pytest.fixture
def adapter() -> SemanticKernelAdapter:
    return SemanticKernelAdapter()


SK_SOURCE = """
import semantic_kernel as sk
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion, OpenAIChatCompletion
from semantic_kernel.functions import kernel_function
from semantic_kernel.prompt_template import PromptTemplateConfig

kernel = Kernel()

@kernel_function(name="search", description="Search the web")
def web_search(query: str) -> str:
    return "results"

@kernel_function
async def summarize(text: str) -> str:
    return text[:100]

service = AzureChatCompletion(deployment_name="gpt-4o", endpoint="https://...")
kernel.add_service(service)

kernel.add_plugin(web_search, plugin_name="SearchPlugin")

template_config = PromptTemplateConfig(template="You are a helpful assistant. Task: {{$input}}")
"""

MINIMAL_SK_SOURCE = """
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion

k = Kernel()
svc = OpenAIChatCompletion(ai_model_id="gpt-4-turbo")
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_kernel_detected(adapter: SemanticKernelAdapter) -> None:
    """Kernel() → AGENT node."""
    nodes, _ = adapter.extract(Path("app.py"), SK_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert any(n.name == "Kernel" for n in agent_nodes)


def test_kernel_function_decorator(adapter: SemanticKernelAdapter) -> None:
    """@kernel_function decorated functions → TOOL nodes."""
    nodes, _ = adapter.extract(Path("app.py"), SK_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    names = {n.name for n in tool_nodes}
    assert "web_search" in names or "summarize" in names


def test_add_plugin_detected(adapter: SemanticKernelAdapter) -> None:
    """kernel.add_plugin(...) → TOOL node."""
    nodes, _ = adapter.extract(Path("app.py"), SK_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert len(tool_nodes) >= 1


def test_azure_chat_completion_detected(adapter: SemanticKernelAdapter) -> None:
    """AzureChatCompletion → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), SK_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert any("AzureChatCompletion" in n.name for n in model_nodes)


def test_openai_chat_completion_detected(adapter: SemanticKernelAdapter) -> None:
    """OpenAIChatCompletion → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), MINIMAL_SK_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1


def test_prompt_template_detected(adapter: SemanticKernelAdapter) -> None:
    """PromptTemplateConfig → PROMPT node."""
    nodes, _ = adapter.extract(Path("app.py"), SK_SOURCE)
    prompt_nodes = [n for n in nodes if n.component_type == NodeType.PROMPT]
    assert len(prompt_nodes) >= 1


def test_edges_created(adapter: SemanticKernelAdapter) -> None:
    """USES and CALLS edges are created."""
    nodes, edges = adapter.extract(Path("app.py"), SK_SOURCE)
    rel_types = {e.relationship_type for e in edges}
    assert EdgeRelationshipType.USES in rel_types or EdgeRelationshipType.CALLS in rel_types


def test_empty_source_returns_empty(adapter: SemanticKernelAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: SemanticKernelAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []
