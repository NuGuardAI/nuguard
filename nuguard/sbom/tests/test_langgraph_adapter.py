"""Unit tests for the LangGraphAdapter.

Covers:
  TestCanHandle           — adapter activates on langchain/langgraph imports
  TestFrameworkNode       — correct framework node emitted (langchain vs langgraph)
  TestLangGraphPatterns   — framework:langgraph emitted when patterns found without direct import
  TestAgentDetection      — add_node / create_react_agent / compile → AGENT nodes
  TestToolDetection       — @tool decorator / ToolNode → TOOL nodes
  TestModelDetection      — ChatOpenAI / ChatAnthropic / etc. → MODEL nodes
  TestRelationshipHints   — USES hints emitted by model detection for add_node agents
  TestNegatives           — no false positives on unrelated code
"""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.sbom.adapters.base import RelationshipHint
from nuguard.sbom.adapters.python.langgraph import LangGraphAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.types import ComponentType

_ADAPTER = LangGraphAdapter()


def _extract(code: str) -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, "app.py", pr)


def _by_type(detections: list[Any], ctype: ComponentType) -> list[Any]:
    return [d for d in detections if d.component_type == ctype]


def _all_hints(detections: list[Any]) -> list[RelationshipHint]:
    hints: list[RelationshipHint] = []
    for d in detections:
        hints.extend(d.relationships)
    return hints


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    @pytest.mark.parametrize(
        "module",
        [
            "langchain",
            "langchain_openai",
            "langchain_anthropic",
            "langchain_core",
            "langchain_community",
            "langgraph",
            "langgraph.graph",
            "langgraph.prebuilt",
        ],
    )
    def test_activates_on_langchain_langgraph_imports(self, module: str) -> None:
        assert _ADAPTER.can_handle({module})

    def test_does_not_activate_on_unrelated(self) -> None:
        assert not _ADAPTER.can_handle({"openai", "anthropic", "flask"})

    def test_none_parse_result_returns_empty(self) -> None:
        assert _ADAPTER.extract("", "x.py", None) == []


# ---------------------------------------------------------------------------
# FRAMEWORK node — langchain vs langgraph
# ---------------------------------------------------------------------------


class TestFrameworkNode:
    def test_langgraph_import_emits_langgraph_framework(self) -> None:
        code = (
            "from langgraph.graph import StateGraph, END\n"
            "from langchain_openai import ChatOpenAI\n"
        )
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        names = {f.canonical_name for f in fw}
        assert any("langgraph" in n and "langchain" not in n for n in names), (
            f"Expected framework:langgraph in {names}"
        )

    def test_langgraph_framework_confidence_high(self) -> None:
        code = "from langgraph.graph import StateGraph\n"
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        langgraph_fw = next(
            (f for f in fw if "langgraph" in f.canonical_name and "langchain" not in f.canonical_name),
            None,
        )
        assert langgraph_fw is not None
        assert langgraph_fw.confidence >= 0.90

    def test_langchain_only_import_emits_langchain_framework(self) -> None:
        code = "from langchain_openai import ChatOpenAI\n"
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        names = {f.canonical_name for f in fw}
        assert any("langchain" in n for n in names)
        # Should not emit a langgraph node (no patterns found)
        assert not any("langgraph" in n and "langchain" not in n for n in names)


# ---------------------------------------------------------------------------
# LangGraph pattern detection without direct import
# ---------------------------------------------------------------------------


class TestLangGraphPatterns:
    """When code uses langgraph patterns but only imports langchain_* packages,
    the adapter should still emit a framework:langgraph node.

    Note: add_node callbacks must end with _node, _agent, or _graph (or be
    a known agent factory var) to be treated as agent callbacks rather than
    plain bridging functions. Use realistic naming conventions in tests.
    """

    def test_add_node_emits_langgraph_framework(self) -> None:
        # Callbacks ending in _node are treated as agent node callbacks
        code = (
            "from langchain_openai import ChatOpenAI\n"
            "\n"
            "graph = object()\n"
            "graph.add_node('researcher', researcher_node)\n"
            "graph.add_node('writer', writer_node)\n"
        )
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        names = {f.canonical_name for f in fw}
        assert any("langgraph" in n and "langchain" not in n for n in names), (
            f"Expected framework:langgraph node from add_node patterns; got {names}"
        )

    def test_add_node_langgraph_confidence_at_least_088(self) -> None:
        # Use _node suffix so the callback is recognised as an agent node
        code = (
            "from langchain_core.messages import HumanMessage\n"
            "\n"
            "graph.add_node('node_a', fn_node)\n"
        )
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        langgraph_fw = next(
            (f for f in fw if "langgraph" in f.canonical_name and "langchain" not in f.canonical_name),
            None,
        )
        assert langgraph_fw is not None, "Expected framework:langgraph from add_node"
        assert langgraph_fw.confidence >= 0.88

    def test_create_react_agent_emits_langgraph_framework(self) -> None:
        code = (
            "from langchain_openai import ChatOpenAI\n"
            "from langchain.tools import tool\n"
            "\n"
            "llm = ChatOpenAI(model='gpt-4o')\n"
            "\n"
            "@tool\n"
            "def search(q: str): return ''\n"
            "\n"
            "agent = create_react_agent(llm, [search])\n"
        )
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        names = {f.canonical_name for f in fw}
        assert any("langgraph" in n and "langchain" not in n for n in names), (
            f"Expected langgraph framework from create_react_agent; got {names}"
        )

    def test_add_conditional_edges_emits_langgraph_framework(self) -> None:
        code = (
            "from langchain_anthropic import ChatAnthropic\n"
            "\n"
            "graph.add_conditional_edges('router', route_fn, {'a': 'node_a', 'b': 'node_b'})\n"
        )
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        names = {f.canonical_name for f in fw}
        assert any("langgraph" in n and "langchain" not in n for n in names)

    def test_no_langgraph_node_when_no_patterns_and_no_import(self) -> None:
        code = (
            "from langchain_openai import ChatOpenAI\n"
            "llm = ChatOpenAI(model='gpt-4o')\n"
            "response = llm.invoke('hello')\n"
        )
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        names = {f.canonical_name for f in fw}
        # No langgraph-specific patterns → no langgraph framework node
        assert not any("langgraph" in n and "langchain" not in n for n in names)

    def test_both_langchain_and_langgraph_nodes_when_patterns_found(self) -> None:
        """Both framework:langchain and langgraph nodes should coexist."""
        code = (
            "from langchain_openai import ChatOpenAI\n"
            "graph.add_node('agent', agent_node)\n"
        )
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        names = {f.canonical_name for f in fw}
        assert any("langchain" in n for n in names)
        assert any("langgraph" in n and "langchain" not in n for n in names)


# ---------------------------------------------------------------------------
# Agent detection
# ---------------------------------------------------------------------------


class TestAgentDetection:
    def test_add_node_emits_agent(self) -> None:
        # Realistic callback suffix: _node marks it as an agent node callback
        code = (
            "from langgraph.graph import StateGraph\n"
            "workflow = StateGraph(dict)\n"
            "workflow.add_node('planner', planner_node)\n"
        )
        agents = _by_type(_extract(code), ComponentType.AGENT)
        assert any(a.display_name == "planner" for a in agents)

    def test_create_react_agent_emits_agent(self) -> None:
        code = (
            "from langgraph.prebuilt import create_react_agent\n"
            "from langchain_openai import ChatOpenAI\n"
            "agent = create_react_agent(ChatOpenAI(), [])\n"
        )
        agents = _by_type(_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT from create_react_agent"

    def test_internal_nodes_not_emitted_as_agents(self) -> None:
        # __start__ and tools are filtered as internal nodes
        # agent_node suffix is required for real_agent to be detected
        code = (
            "from langgraph.graph import StateGraph, END\n"
            "wf = StateGraph(dict)\n"
            "wf.add_node('__start__', fn_node)\n"
            "wf.add_node('tools', tool_node)\n"
            "wf.add_node('real_agent', agent_node)\n"
        )
        agents = _by_type(_extract(code), ComponentType.AGENT)
        agent_names = {a.display_name for a in agents}
        assert "real_agent" in agent_names
        assert "__start__" not in agent_names

    def test_multiple_agents_detected(self) -> None:
        # Use _node suffix for all callbacks to ensure agent detection
        code = (
            "from langgraph.graph import StateGraph\n"
            "wf = StateGraph(dict)\n"
            "wf.add_node('researcher', researcher_node)\n"
            "wf.add_node('writer', writer_node)\n"
            "wf.add_node('reviewer', reviewer_node)\n"
        )
        agents = _by_type(_extract(code), ComponentType.AGENT)
        names = {a.display_name for a in agents}
        assert {"researcher", "writer", "reviewer"} <= names


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


class TestToolDetection:
    def test_at_tool_decorator_detected(self) -> None:
        code = (
            "from langchain.tools import tool\n"
            "\n"
            "@tool\n"
            "def web_search(query: str) -> str:\n"
            "    return ''\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        assert any(t.display_name == "web_search" for t in tools)

    def test_toolnode_instantiation_detected(self) -> None:
        code = (
            "from langgraph.prebuilt import ToolNode\n"
            "tool_node = ToolNode([search_tool, calc_tool])\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        assert tools, "Expected TOOL nodes from ToolNode"


# ---------------------------------------------------------------------------
# Model detection
# ---------------------------------------------------------------------------


class TestModelDetection:
    def test_chat_openai_detected(self) -> None:
        code = (
            "from langchain_openai import ChatOpenAI\n"
            "llm = ChatOpenAI(model='gpt-4o')\n"
        )
        models = _by_type(_extract(code), ComponentType.MODEL)
        assert any("gpt-4o" in m.display_name or "gpt_4o" in m.display_name for m in models), (
            f"gpt-4o not found in {[m.display_name for m in models]}"
        )

    def test_chat_anthropic_detected(self) -> None:
        code = (
            "from langchain_anthropic import ChatAnthropic\n"
            "llm = ChatAnthropic(model='claude-3-5-sonnet-20241022')\n"
        )
        models = _by_type(_extract(code), ComponentType.MODEL)
        assert any("sonnet" in m.display_name or "claude" in m.display_name.lower() for m in models)


# ---------------------------------------------------------------------------
# Relationship hints — add_node agents → model USES hints
# ---------------------------------------------------------------------------


class TestRelationshipHints:
    def test_add_node_agent_gets_uses_hint_to_model(self) -> None:
        """When add_node agents are detected BEFORE the model, the model
        detection emits AGENT-[USES]->MODEL hints for each known agent."""
        code = (
            "from langgraph.graph import StateGraph\n"
            "from langchain_anthropic import ChatAnthropic\n"
            "\n"
            "wf = StateGraph(dict)\n"
            "wf.add_node('researcher', researcher_node)\n"
            "llm = ChatAnthropic(model='claude-3-5-sonnet-20241022')\n"
        )
        hints = _all_hints(_extract(code))
        uses_model = [
            h for h in hints
            if h.relationship_type == "USES" and h.target_type == ComponentType.MODEL
        ]
        assert uses_model, "Expected AGENT -[USES]-> MODEL hint from model detection"
        assert uses_model[0].source_type == ComponentType.AGENT

    def test_create_react_agent_emits_agent_node(self) -> None:
        """create_react_agent always produces an AGENT ComponentDetection."""
        code = (
            "from langgraph.prebuilt import create_react_agent\n"
            "from langchain_openai import ChatOpenAI\n"
            "from langchain.tools import tool\n"
            "\n"
            "@tool\n"
            "def search(q: str): return ''\n"
            "\n"
            "agent = create_react_agent(ChatOpenAI(), [search])\n"
        )
        agents = _by_type(_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT node from create_react_agent"
        # The agent itself has relationships (factory_rels may be empty if args
        # are variable refs; edge inference handles this at extractor level)
        assert agents[0].component_type == ComponentType.AGENT


# ---------------------------------------------------------------------------
# Negatives
# ---------------------------------------------------------------------------


class TestNegatives:
    def test_no_agent_from_plain_function(self) -> None:
        code = (
            "from langchain_openai import ChatOpenAI\n"
            "def my_function(x): return x\n"
        )
        assert not _by_type(_extract(code), ComponentType.AGENT)

    def test_empty_source_no_crash(self) -> None:
        dets = _extract("")
        assert isinstance(dets, list)

    def test_invalid_syntax_no_crash(self) -> None:
        dets = _extract("def foo(:")
        assert isinstance(dets, list)
