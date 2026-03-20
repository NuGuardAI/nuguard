"""Tests for the OpenAI Agents SDK adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.models.sbom import EdgeRelationshipType, NodeType
from nuguard.sbom.extractor.framework_adapters.openai_agents import OpenAIAgentsAdapter


@pytest.fixture
def adapter() -> OpenAIAgentsAdapter:
    return OpenAIAgentsAdapter()


AGENTS_SOURCE = """
from agents import Agent, Runner, function_tool, InputGuardrail, OutputGuardrail

@function_tool
def get_weather(city: str) -> str:
    \"\"\"Get weather for a city.\"\"\"
    return "sunny"

agent = Agent(
    name="WeatherAgent",
    instructions="You are a helpful weather assistant that provides accurate forecasts.",
    tools=[get_weather],
    model="gpt-4o",
)
guardrail = InputGuardrail()
output_guard = OutputGuardrail()

result = Runner.run(agent, "What is the weather in Paris?")
"""

MINIMAL_SOURCE = """
from agents import Agent

a = Agent(name="MyBot", instructions="Help users.")
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_agent_detected(adapter: OpenAIAgentsAdapter) -> None:
    """Agent(...) → AGENT node."""
    nodes, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert len(agent_nodes) >= 1
    assert any(n.name == "WeatherAgent" for n in agent_nodes)


def test_function_tool_decorator(adapter: OpenAIAgentsAdapter) -> None:
    """@function_tool decorated function → TOOL node."""
    nodes, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert any(n.name == "get_weather" for n in tool_nodes)


def test_input_guardrail_detected(adapter: OpenAIAgentsAdapter) -> None:
    """InputGuardrail → GUARDRAIL node."""
    nodes, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    guardrail_nodes = [n for n in nodes if n.component_type == NodeType.GUARDRAIL]
    assert len(guardrail_nodes) >= 1


def test_output_guardrail_detected(adapter: OpenAIAgentsAdapter) -> None:
    """OutputGuardrail → GUARDRAIL node."""
    nodes, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    guardrail_nodes = [n for n in nodes if n.component_type == NodeType.GUARDRAIL]
    assert any(n.name == "OutputGuardrail" for n in guardrail_nodes)


def test_model_detected(adapter: OpenAIAgentsAdapter) -> None:
    """model= kwarg → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert any(n.name == "gpt-4o" for n in model_nodes)


def test_prompt_from_instructions(adapter: OpenAIAgentsAdapter) -> None:
    """Long instructions string → PROMPT node."""
    nodes, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    prompt_nodes = [n for n in nodes if n.component_type == NodeType.PROMPT]
    assert len(prompt_nodes) >= 1


def test_guardrail_protects_agent_edge(adapter: OpenAIAgentsAdapter) -> None:
    """GUARDRAIL -PROTECTS-> AGENT edge is created."""
    nodes, edges = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    protects_edges = [e for e in edges if e.relationship_type == EdgeRelationshipType.PROTECTS]
    assert len(protects_edges) >= 1


def test_agent_uses_model_edge(adapter: OpenAIAgentsAdapter) -> None:
    """AGENT -USES-> MODEL edge is created."""
    nodes, edges = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    uses_edges = [e for e in edges if e.relationship_type == EdgeRelationshipType.USES]
    assert len(uses_edges) >= 1


def test_agent_calls_tool_edge(adapter: OpenAIAgentsAdapter) -> None:
    """AGENT -CALLS-> TOOL edge is created."""
    nodes, edges = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    calls_edges = [e for e in edges if e.relationship_type == EdgeRelationshipType.CALLS]
    assert len(calls_edges) >= 1


def test_empty_source_returns_empty(adapter: OpenAIAgentsAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: OpenAIAgentsAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []


def test_node_ids_are_stable(adapter: OpenAIAgentsAdapter) -> None:
    nodes1, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    nodes2, _ = adapter.extract(Path("app.py"), AGENTS_SOURCE)
    ids1 = {n.id for n in nodes1}
    ids2 = {n.id for n in nodes2}
    assert ids1 == ids2
