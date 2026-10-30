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


def _extract(code: str, file_path: str = "app.py") -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, file_path, pr)


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


class TestTypedSubAgentMappings:
    def test_deepagents_only_does_not_emit_langchain_framework(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "worker: SubAgent = {'name': 'worker'}\n"
        )

        frameworks = _by_type(_extract(code), ComponentType.FRAMEWORK)

        assert {framework.canonical_name for framework in frameworks} == {"langgraph"}

    def test_deepagents_only_does_not_enable_legacy_langgraph_scans(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "class StateGraph: pass\n"
            "graph = StateGraph()\n"
            "def create_react_agent(): return object()\n"
            "agent = create_react_agent()\n"
            "router.add_conditional_edges('a', choose)\n"
        )

        assert _extract(code) == []

    @pytest.mark.parametrize(
        "code",
        [
            "if False:\n"
            "    import langgraph\n"
            "def create_react_agent(): return object()\n"
            "agent = create_react_agent()\n",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    import langgraph\n"
            "def create_react_agent(): return object()\n"
            "agent = create_react_agent()\n",
        ],
    )
    def test_inactive_imports_do_not_enable_legacy_langgraph_scans(
        self, code: str
    ) -> None:
        assert _extract(code) == []

    def test_runtime_type_checking_branch_enables_legacy_langgraph_scans(
        self,
    ) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            "if not TYPE_CHECKING:\n"
            "    from langgraph.prebuilt import create_react_agent\n"
            "agent = create_react_agent()\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"agent"}

    @pytest.mark.parametrize(
        "unrelated_binding",
        [
            "def helper(TYPE_CHECKING):\n    return TYPE_CHECKING\n",
            "def helper():\n    TYPE_CHECKING = True\n    return TYPE_CHECKING\n",
            "if False:\n    TYPE_CHECKING = True\n",
        ],
    )
    def test_unrelated_bindings_do_not_activate_type_only_legacy_imports(
        self, unrelated_binding: str
    ) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            f"{unrelated_binding}"
            "if TYPE_CHECKING:\n"
            "    import langgraph\n"
            "def create_react_agent(): return object()\n"
            "agent = create_react_agent()\n"
        )

        assert _extract(code) == []

    def test_function_parameter_guard_keeps_potential_legacy_import(self) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            "def build(TYPE_CHECKING):\n"
            "    if TYPE_CHECKING:\n"
            "        from langgraph.prebuilt import create_react_agent\n"
            "        agent = create_react_agent()\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"agent"}

    def test_direct_literal_mapping_links_only_exact_model_and_tool_targets(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "from langchain_openai import ChatOpenAI\n"
            "from langchain_core.tools import tool\n\n"
            "@tool\n"
            "def web_search(query: str) -> str:\n"
            "    return query\n\n"
            "llm = ChatOpenAI(model='gpt-4o')\n"
            "researcher: SubAgent = {\n"
            "    'name': 'researcher',\n"
            "    'model': 'gpt-4o',\n"
            "    'tools': ['web_search', 'missing_tool'],\n"
            "}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)
        researcher = next(agent for agent in agents if agent.display_name == "researcher")

        assert researcher.metadata["subagent_type"] == "SubAgent"
        relationship_targets = {
            (rel.relationship_type, rel.target_type, rel.target_canonical)
            for rel in researcher.relationships
        }
        assert relationship_targets == {
            ("USES", ComponentType.MODEL, "gpt_4o"),
            ("CALLS", ComponentType.TOOL, "langchain_tool_web_search"),
        }

    def test_literal_mapping_entries_are_detected(self) -> None:
        code = (
            "from deepagents import AsyncSubAgent, CompiledSubAgent\n"
            "workers: list[AsyncSubAgent] = [\n"
            "    {'name': 'writer'},\n"
            "    {'name': 'reviewer'},\n"
            "]\n"
            "compiled: dict[str, CompiledSubAgent] = {\n"
            "    'planner': {'name': 'planner', 'runnable': graph},\n"
            "}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)
        names = {agent.display_name for agent in agents}

        assert names == {"writer", "reviewer", "planner"}

    def test_proven_sequence_and_mapping_aliases_are_detected(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "from typing import Sequence as SubAgentSequence\n"
            "import collections.abc as cabc\n"
            "workers: SubAgentSequence[SubAgent] = [{'name': 'sequenced'}]\n"
            "registry: cabc.Mapping[str, SubAgent] = {\n"
            "    'mapped': {'name': 'mapped'},\n"
            "}\n"
            "fixed: tuple[SubAgent, ...] = ({'name': 'tupled'},)\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {
            "sequenced",
            "mapped",
            "tupled",
        }

    def test_dynamic_and_ambiguous_mappings_are_not_inferred(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "runtime_name = load_name()\n"
            "dynamic: SubAgent = {'name': runtime_name}\n"
            "ambiguous: list[SubAgent | str] = [{'name': 'ambiguous'}]\n"
            "untyped = {'name': 'untyped'}\n"
            "workers: list[SubAgent] = []\n"
            "for item in configs:\n"
            "    workers.append({'name': item.name})\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)
        names = {agent.display_name for agent in agents}

        assert names.isdisjoint({"ambiguous", "untyped"})
        assert not agents

    def test_dynamic_model_and_tool_values_do_not_create_relationship_hints(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "worker: SubAgent = {\n"
            "    'name': 'worker',\n"
            "    'model': runtime_model,\n"
            "    'tools': [runtime_tool],\n"
            "}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)
        worker = next(agent for agent in agents if agent.display_name == "worker")

        assert worker.relationships == []

    def test_literal_mapping_links_exact_callable_tool_reference(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def web_search(query: str) -> str:\n"
            "    return query\n"
            "worker: SubAgent = {\n"
            "    'name': 'worker',\n"
            "    'tools': [web_search],\n"
            "}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)
        worker = next(agent for agent in agents if agent.display_name == "worker")

        assert {
            (relationship.relationship_type, relationship.target_canonical)
            for relationship in worker.relationships
        } == {("CALLS", "langchain_tool_web_search")}

    @pytest.mark.parametrize("tool_value", ["build_tool()", "runtime_tool.attr"])
    def test_literal_mapping_does_not_link_dynamic_tool_expression(
        self, tool_value: str
    ) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def web_search(query: str) -> str:\n"
            "    return query\n"
            f"worker: SubAgent = {{'name': 'worker', 'tools': [{tool_value}]}}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)
        worker = next(agent for agent in agents if agent.display_name == "worker")

        assert worker.relationships == []

    @pytest.mark.parametrize(
        "file_path",
        ["tests/test_agents.py", "test_agents.py", "agents_test.py", "conftest.py"],
    )
    def test_test_path_suppresses_typed_subagent_mapping(self, file_path: str) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "worker: SubAgent = {'name': 'fixture-worker'}\n"
        )

        agents = _by_type(_extract(code, file_path), ComponentType.AGENT)

        assert agents == []

    def test_typed_subagent_mapping_requires_trusted_import_provenance(self) -> None:
        code = (
            "class SubAgent: pass\n"
            "import fake\n"
            "from fake import AsyncSubAgent\n"
            "local: SubAgent = {'name': 'local-agent'}\n"
            "qualified: fake.CompiledSubAgent = {'name': 'fake-agent'}\n"
            "imported: AsyncSubAgent = {'name': 'fake-import-agent'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        "relative_import",
        [
            "from .deepagents import SubAgent",
            "from ..managed_deepagents import SubAgent",
        ],
    )
    def test_typed_subagent_mapping_rejects_relative_package_lookalikes(
        self, relative_import: str
    ) -> None:
        code = f"{relative_import}\nworker: SubAgent = {{'name': 'relative'}}\n"

        assert _extract(code) == []

    def test_typed_subagent_mapping_supports_trusted_import_aliases(self) -> None:
        code = (
            "from deepagents import SubAgent as WorkerSpec\n"
            "import managed_deepagents as managed\n"
            "one: WorkerSpec = {'name': 'researcher'}\n"
            "def build():\n"
            "    two: managed.CompiledSubAgent = {'name': 'writer'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"researcher", "writer"}

    @pytest.mark.parametrize(
        "rebound",
        [
            "WorkerSpec = FakeSpec\nworker: WorkerSpec = {'name': 'rebound'}\n",
            "import fake as WorkerSpec\nworker: WorkerSpec = {'name': 'reimported'}\n",
        ],
    )
    def test_typed_subagent_mapping_rejects_module_symbol_rebinding(
        self, rebound: str
    ) -> None:
        code = "from deepagents import SubAgent as WorkerSpec\n" + rebound

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_nested_import_and_rebinding_follow_source_order(self) -> None:
        code = (
            "if enabled:\n"
            "    from deepagents import SubAgent\n"
            "    before: SubAgent = {'name': 'before-rebind'}\n"
            "    SubAgent = FakeSpec\n"
            "    after: SubAgent = {'name': 'after-rebind'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"before-rebind"}

    @pytest.mark.parametrize(
        "code",
        [
            "if False:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'dead'}\n",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'type-only'}\n",
            "import typing as t\n"
            "if t.TYPE_CHECKING:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'qualified-type-only'}\n",
        ],
    )
    def test_typed_subagent_mapping_rejects_nonruntime_branches(
        self, code: str
    ) -> None:
        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        "condition",
        [
            "TYPE_CHECKING and enabled",
            "TYPE_CHECKING is True",
            "TYPE_CHECKING or False",
            "False and enabled",
        ],
    )
    def test_typed_subagent_mapping_rejects_composite_nonruntime_branches(
        self, condition: str
    ) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            f"if {condition}:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'composite-dead'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        "condition", ["TYPE_CHECKING or enabled", "not TYPE_CHECKING and enabled"]
    )
    def test_typed_subagent_mapping_keeps_unknown_runtime_branches(
        self, condition: str
    ) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            f"if {condition}:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'runtime-possible'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"runtime-possible"}

    def test_typed_subagent_mapping_accepts_runtime_type_checking_branch(self) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            "from deepagents import SubAgent\n"
            "if not TYPE_CHECKING:\n"
            "    worker: SubAgent = {'name': 'runtime-worker'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"runtime-worker"}

    def test_dead_prior_branch_preserves_runtime_provenance(self) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            "from deepagents import SubAgent\n"
            "if False:\n"
            "    TYPE_CHECKING = True\n"
            "    SubAgent = FakeSpec\n"
            "live: SubAgent = {'name': 'live-after-dead-branch'}\n"
            "if TYPE_CHECKING:\n"
            "    dead: SubAgent = {'name': 'dead-type-only'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {
            "live-after-dead-branch"
        }

    def test_prior_if_join_preserves_identical_trusted_imports(self) -> None:
        code = (
            "if enabled:\n"
            "    from deepagents import SubAgent\n"
            "else:\n"
            "    from deepagents import SubAgent\n"
            "worker: SubAgent = {'name': 'joined-worker'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"joined-worker"}

    @pytest.mark.parametrize(
        ("setup", "mutation", "annotation"),
        [
            ("from deepagents import SubAgent", "del SubAgent", "SubAgent"),
            ("import deepagents as da", "da.SubAgent = FakeSpec", "da.SubAgent"),
        ],
    )
    def test_prior_if_join_invalidates_delete_or_member_mutation(
        self, setup: str, mutation: str, annotation: str
    ) -> None:
        code = (
            f"{setup}\n"
            "if enabled:\n"
            f"    {mutation}\n"
            f"worker: {annotation} = {{'name': 'branch-untrusted'}}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize("condition", ["0", "None", "''", "()", "[]", "{}"])
    def test_typed_subagent_mapping_rejects_falsy_literal_branches(
        self, condition: str
    ) -> None:
        code = (
            f"if {condition}:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'literal-dead'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_typed_subagent_mapping_rejects_dead_while_body(self) -> None:
        code = (
            "while False:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'loop-dead'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize("empty_iterable", ["[]", "()", "{}", "''", "b''"])
    def test_typed_subagent_mapping_rejects_proven_empty_for_body(
        self, empty_iterable: str
    ) -> None:
        code = (
            "from deepagents import SubAgent\n"
            f"for item in {empty_iterable}:\n"
            "    worker: SubAgent = {'name': 'dead-for'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_empty_for_does_not_apply_unexecuted_rebinding(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "for SubAgent in []:\n"
            "    pass\n"
            "worker: SubAgent = {'name': 'still-trusted'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"still-trusted"}

    @pytest.mark.parametrize("condition", ["[1]", "{'enabled': True}", "{**runtime}"])
    def test_typed_subagent_mapping_keeps_potential_literal_branches(
        self, condition: str
    ) -> None:
        code = (
            f"if {condition}:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'literal-live'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"literal-live"}

    @pytest.mark.parametrize(
        ("comparison", "expected"),
        [("TYPE_CHECKING is 0", set()), ("TYPE_CHECKING == 0", {"comparison-live"})],
    )
    def test_type_checking_comparisons_preserve_python_semantics(
        self, comparison: str, expected: set[str]
    ) -> None:
        code = (
            "from typing import TYPE_CHECKING\n"
            f"if {comparison}:\n"
            "    from deepagents import SubAgent\n"
            "    worker: SubAgent = {'name': 'comparison-live'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == expected

    def test_try_else_replays_successful_try_bindings(self) -> None:
        code = (
            "try:\n"
            "    from deepagents import SubAgent\n"
            "except ImportError:\n"
            "    pass\n"
            "else:\n"
            "    worker: SubAgent = {'name': 'try-else-worker'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"try-else-worker"}

    def test_except_handler_preserves_untouched_trusted_binding(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "try:\n"
            "    unrelated = prepare()\n"
            "    risky()\n"
            "except Exception:\n"
            "    worker: SubAgent = {'name': 'handler-trusted'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"handler-trusted"}

    @pytest.mark.parametrize(
        ("setup", "mutation", "annotation"),
        [
            ("from deepagents import SubAgent", "del SubAgent", "SubAgent"),
            ("import deepagents as da", "da.SubAgent = FakeSpec", "da.SubAgent"),
        ],
    )
    def test_except_handler_invalidates_possible_try_prefix_mutation(
        self, setup: str, mutation: str, annotation: str
    ) -> None:
        code = (
            f"{setup}\n"
            "try:\n"
            f"    {mutation}\n"
            "    risky()\n"
            "except Exception:\n"
            f"    worker: {annotation} = {{'name': 'handler-untrusted'}}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("    unrelated = work()\n", {"loop-else-trusted"}),
            ("    del SubAgent\n", set()),
            ("    del SubAgent\n    continue\n", set()),
            ("    del SubAgent\n    break\n", {"loop-else-trusted"}),
        ],
    )
    def test_while_else_joins_zero_and_body_exit_states(
        self, body: str, expected: set[str]
    ) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "while enabled:\n"
            f"{body}"
            "else:\n"
            "    worker: SubAgent = {'name': 'loop-else-trusted'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == expected

    @pytest.mark.parametrize("guard", ["True", "1 == 1"])
    def test_exhaustive_prior_match_case_makes_later_case_nonruntime(
        self, guard: str
    ) -> None:
        code = (
            "match value:\n"
            f"    case _ if {guard}:\n"
            "        pass\n"
            "    case _:\n"
            "        from deepagents import SubAgent\n"
            "        worker: SubAgent = {'name': 'dead-match-worker'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_unknown_prior_match_guard_keeps_later_case_reachable(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "match value:\n"
            "    case _ if enabled:\n"
            "        pass\n"
            "    case _:\n"
            "        worker: SubAgent = {'name': 'reachable-match-worker'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {
            "reachable-match-worker"
        }

    @pytest.mark.parametrize(
        "try_body",
        [
            "    pass\n",
            "    from deepagents import SubAgent\n    SubAgent = FakeSpec\n",
        ],
    )
    def test_try_else_requires_successful_trusted_binding(
        self, try_body: str
    ) -> None:
        code = (
            "try:\n"
            f"{try_body}"
            "except ImportError:\n"
            "    pass\n"
            "else:\n"
            "    worker: SubAgent = {'name': 'untrusted-try-else'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        ("import_line", "mutation", "annotation"),
        [
            ("from deepagents import SubAgent", "SubAgent = FakeSpec", "SubAgent"),
            ("from deepagents import SubAgent", "del SubAgent", "SubAgent"),
            ("import deepagents as da", "da.SubAgent = FakeSpec", "da.SubAgent"),
        ],
    )
    def test_try_finally_invalidates_ambiguous_trusted_bindings(
        self, import_line: str, mutation: str, annotation: str
    ) -> None:
        code = (
            f"{import_line}\n"
            "try:\n"
            f"    {mutation}\n"
            "finally:\n"
            f"    worker: {annotation} = {{'name': 'finally-untrusted'}}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_try_finally_preserves_untouched_trusted_binding(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "try:\n"
            "    unrelated = make_value()\n"
            "finally:\n"
            "    worker: SubAgent = {'name': 'finally-trusted'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"finally-trusted"}

    @pytest.mark.parametrize(
        "definition",
        [
            "def helper(value=(SubAgent := FakeSpec)):\n    pass\n",
            "@decorate(SubAgent := FakeSpec)\ndef helper():\n    pass\n",
            "class Helper((SubAgent := FakeSpec)):\n    pass\n",
        ],
    )
    def test_definition_headers_invalidate_rebound_trusted_binding(
        self, definition: str
    ) -> None:
        code = (
            "from deepagents import SubAgent\n"
            f"{definition}"
            "worker: SubAgent = {'name': 'definition-untrusted'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_definition_header_preserves_untouched_trusted_binding(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "@decorate(other_name)\n"
            "def helper(value=other_default):\n"
            "    pass\n"
            "worker: SubAgent = {'name': 'definition-trusted'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"definition-trusted"}

    @pytest.mark.parametrize(
        "body",
        [
            "def build(SubAgent):\n"
            "    worker: SubAgent = {'name': 'parameter-shadow'}\n",
            "def build():\n"
            "    worker: SubAgent = {'name': 'local-shadow'}\n"
            "    SubAgent = FakeSpec\n",
        ],
    )
    def test_typed_subagent_mapping_rejects_function_scope_shadowing(
        self, body: str
    ) -> None:
        code = "from deepagents import SubAgent\n" + body

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize("declaration", ["global", "nonlocal"])
    def test_declared_outer_type_rebound_after_annotation_remains_trusted(
        self, declaration: str
    ) -> None:
        inner = (
            f"        {declaration} SubAgent\n"
            "        worker: SubAgent = {'name': 'declared-outer'}\n"
            "        SubAgent = FakeSpec\n"
        )
        if declaration == "global":
            code = (
                "from deepagents import SubAgent\n"
                "def build():\n"
                f"{inner.replace('        ', '    ')}"
            )
        else:
            code = (
                "def outer():\n"
                "    from deepagents import SubAgent\n"
                "    def build():\n"
                f"{inner}"
            )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"declared-outer"}

    @pytest.mark.parametrize("declaration", ["global", "nonlocal"])
    @pytest.mark.parametrize(
        "mutation",
        ["SubAgent = FakeSpec", "del SubAgent"],
    )
    def test_declared_outer_type_mutated_before_annotation_is_rejected(
        self, declaration: str, mutation: str
    ) -> None:
        inner = (
            f"        {declaration} SubAgent\n"
            f"        {mutation}\n"
            "        worker: SubAgent = {'name': 'mutated-outer'}\n"
        )
        if declaration == "global":
            code = (
                "from deepagents import SubAgent\n"
                "def build():\n"
                f"{inner.replace('        ', '    ')}"
            )
        else:
            code = (
                "def outer():\n"
                "    from deepagents import SubAgent\n"
                "    def build():\n"
                f"{inner}"
            )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize("declaration", ["global", "nonlocal"])
    def test_declared_outer_namespace_member_mutated_before_annotation_is_rejected(
        self, declaration: str
    ) -> None:
        inner = (
            f"        {declaration} da\n"
            "        da.SubAgent = FakeSpec\n"
            "        worker: da.SubAgent = {'name': 'mutated-namespace'}\n"
        )
        if declaration == "global":
            code = (
                "import deepagents as da\n"
                "def build():\n"
                f"{inner.replace('        ', '    ')}"
            )
        else:
            code = (
                "def outer():\n"
                "    import deepagents as da\n"
                "    def build():\n"
                f"{inner}"
            )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_typed_subagent_mapping_rejects_namespace_alias_rebinding(self) -> None:
        code = (
            "import deepagents as da\n"
            "da = fake\n"
            "worker: da.SubAgent = {'name': 'namespace-rebound'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        "mutation, annotation",
        [
            ("da.SubAgent = FakeSpec", "da.SubAgent"),
            ("del da.SubAgent", "da.SubAgent"),
            ("typing.Sequence = FakeSequence", "typing.Sequence[SubAgent]"),
        ],
    )
    def test_typed_subagent_mapping_rejects_trusted_namespace_member_mutation(
        self, mutation: str, annotation: str
    ) -> None:
        code = (
            "import deepagents as da\n"
            "from deepagents import SubAgent\n"
            "import typing\n"
            f"{mutation}\n"
            f"worker: {annotation} = {{'name': 'mutated-member'}}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_scope_imports_replay_before_annotation(self) -> None:
        code = (
            "def build():\n"
            "    import deepagents as da\n"
            "    from typing import Sequence as WorkerSequence\n"
            "    workers: WorkerSequence[da.SubAgent] = [\n"
            "        {'name': 'local-import-worker'},\n"
            "    ]\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"local-import-worker"}

    def test_later_class_binding_only_invalidates_later_annotation(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "class Registry:\n"
            "    before: list[SubAgent] = [{'name': 'before-rebind'}]\n"
            "    SubAgent = FakeSpec\n"
            "    after: list[SubAgent] = [{'name': 'after-rebind'}]\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"before-rebind"}

    @pytest.mark.parametrize("wrapper", ["", "def build():\n"])
    def test_comprehension_target_does_not_shadow_enclosing_import(
        self, wrapper: str
    ) -> None:
        indent = "    " if wrapper else ""
        code = (
            "from deepagents import SubAgent\n"
            f"{wrapper}"
            f"{indent}values = [SubAgent for SubAgent in candidates]\n"
            f"{indent}workers: list[SubAgent] = [{{'name': 'comprehension-safe'}}]\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"comprehension-safe"}

    @pytest.mark.parametrize("wrapper", ["", "def build():\n"])
    def test_comprehension_walrus_rebinding_invalidates_enclosing_import(
        self, wrapper: str
    ) -> None:
        indent = "    " if wrapper else ""
        code = (
            "from deepagents import SubAgent\n"
            f"{wrapper}"
            f"{indent}values = [(SubAgent := FakeSpec) for item in items]\n"
            f"{indent}worker: SubAgent = {{'name': 'walrus-rebound'}}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_unproven_generic_containers_are_rejected(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "class Mapping: pass\n"
            "import fake as foreign\n"
            "local: Mapping[str, SubAgent] = {'x': {'name': 'local-mapping'}}\n"
            "remote: foreign.Mapping[str, SubAgent] = {\n"
            "    'x': {'name': 'foreign-mapping'},\n"
            "}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_typed_subagent_mapping_rejects_wrong_generic_positions(self) -> None:
        code = (
            "from typing import Union\n"
            "from deepagents import SubAgent\n"
            "keyed: dict[SubAgent, str] = {'slot': {'name': 'key-agent'}}\n"
            "nested: list[dict[str, SubAgent]] = [{'name': 'nested-agent'}]\n"
            "heterogeneous: tuple[str, SubAgent] = ({'name': 'tuple-agent'},)\n"
            "mixed: tuple[SubAgent, str] = ({'name': 'mixed-tuple-agent'},)\n"
            "united: list[Union[SubAgent, str]] = [{'name': 'union-agent'}]\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        "body",
        [
            "def build():\n    return\n    worker: SubAgent = {'name': 'dead-return'}\n",
            "def build():\n    raise RuntimeError\n    worker: SubAgent = {'name': 'dead-raise'}\n",
            "while enabled:\n    break\n    worker: SubAgent = {'name': 'dead-break'}\n",
            "while enabled:\n    continue\n    worker: SubAgent = {'name': 'dead-continue'}\n",
        ],
    )
    def test_abrupt_exit_makes_later_mapping_unreachable(self, body: str) -> None:
        code = "from deepagents import SubAgent\n" + body

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_raise_named_expression_invalidates_handler_provenance(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "try:\n"
            "    raise (SubAgent := RuntimeError())\n"
            "except RuntimeError:\n"
            "    worker: SubAgent = {'name': 'handler-rebound'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        "mutation",
        [
            "setattr(da, 'SubAgent', FakeSpec)",
            "delattr(da, 'SubAgent')",
            "da.__dict__['SubAgent'] = FakeSpec",
            "del da.__dict__['SubAgent']",
        ],
    )
    def test_dynamic_namespace_member_mutation_invalidates_provenance(
        self, mutation: str
    ) -> None:
        code = (
            "import deepagents as da\n"
            f"{mutation}\n"
            "worker: da.SubAgent = {'name': 'mutated-dynamically'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    @pytest.mark.parametrize(
        "mutation",
        [
            "vars(da)['SubAgent'] = FakeSpec",
            "vars(da).update({'SubAgent': FakeSpec})",
            "da.__dict__.update({'SubAgent': FakeSpec})",
            "da.__dict__.update(runtime_overrides)",
        ],
    )
    def test_namespace_mapping_mutation_invalidates_provenance(
        self, mutation: str
    ) -> None:
        code = (
            "import deepagents as da\n"
            f"{mutation}\n"
            "worker: da.SubAgent = {'name': 'mutated-mapping'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_shadowed_vars_does_not_invalidate_namespace_provenance(self) -> None:
        code = (
            "import deepagents as da\n"
            "def vars(target):\n"
            "    return {}\n"
            "vars(da)['SubAgent'] = FakeSpec\n"
            "worker: da.SubAgent = {'name': 'shadow-safe'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"shadow-safe"}

    def test_namespace_mapping_mutation_inside_comprehension_invalidates_provenance(
        self,
    ) -> None:
        code = (
            "import deepagents as da\n"
            "effects = [da.__dict__.update(runtime_overrides) for item in items]\n"
            "worker: da.SubAgent = {'name': 'comprehension-mutated'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []

    def test_shadowed_setattr_does_not_invalidate_namespace_provenance(self) -> None:
        code = (
            "import deepagents as da\n"
            "def setattr(target, name, value):\n"
            "    return None\n"
            "setattr(da, 'SubAgent', FakeSpec)\n"
            "worker: da.SubAgent = {'name': 'shadow-safe'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"shadow-safe"}

    @pytest.mark.parametrize(
        "mutation",
        [
            "typing.TYPE_CHECKING = True",
            "setattr(typing, 'TYPE_CHECKING', True)",
            "typing.__dict__['TYPE_CHECKING'] = True",
        ],
    )
    def test_mutated_type_checking_member_keeps_runtime_import_reachable(
        self, mutation: str
    ) -> None:
        code = (
            "import typing\n"
            f"{mutation}\n"
            "if typing.TYPE_CHECKING:\n"
            "    import langgraph\n"
            "def create_react_agent(): return object()\n"
            "agent = create_react_agent()\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"agent"}

    @pytest.mark.parametrize(
        "compound",
        [
            "    try:\n        break\n    finally:\n        pass\n",
            "    with context():\n        break\n",
            "    match value:\n        case _:\n            break\n",
        ],
    )
    def test_nested_break_makes_post_loop_mapping_reachable(
        self, compound: str
    ) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "while True:\n"
            f"{compound}"
            "worker: SubAgent = {'name': 'post-loop'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"post-loop"}

    def test_finally_joins_identical_trusted_import_predecessors(self) -> None:
        code = (
            "from deepagents import SubAgent\n"
            "try:\n"
            "    from deepagents import SubAgent\n"
            "except Exception:\n"
            "    from deepagents import SubAgent\n"
            "finally:\n"
            "    worker: SubAgent = {'name': 'joined-finally'}\n"
        )

        agents = _by_type(_extract(code), ComponentType.AGENT)

        assert {agent.display_name for agent in agents} == {"joined-finally"}

    def test_finally_rejects_import_missing_on_exceptional_prefix(self) -> None:
        code = (
            "try:\n"
            "    from deepagents import SubAgent\n"
            "finally:\n"
            "    worker: SubAgent = {'name': 'ambiguous-finally'}\n"
        )

        assert _by_type(_extract(code), ComponentType.AGENT) == []


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
# Dynamic StructuredTool / Tool.from_function detection
# ---------------------------------------------------------------------------


class TestDynamicToolFactoryDetection:
    def test_structured_tool_constructor_uses_name_kwarg(self) -> None:
        code = (
            "from langchain_core.tools import StructuredTool\n\n"
            "direct_tool = StructuredTool(\n"
            "    name='direct',\n"
            "    func=lambda x: x,\n"
            "    description='a direct tool',\n"
            ")\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        assert any(t.display_name == "direct" for t in tools)
        # Must not also fire the generic tool-class-constructor heuristic
        # (section 4b) for the same instantiation — that would double-count
        # under the assigned variable name instead of the real tool name.
        assert not any(t.display_name == "direct_tool" for t in tools)

    def test_structured_tool_from_function_uses_name_kwarg(self) -> None:
        code = (
            "from langchain_core.tools import StructuredTool\n\n"
            "weather_tool = StructuredTool.from_function(\n"
            "    func=lambda city: city,\n"
            "    name='get_weather',\n"
            "    description='Get the weather',\n"
            ")\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        matches = [t for t in tools if t.display_name == "get_weather"]
        assert matches
        assert matches[0].metadata.get("dynamic_name") is not True

    def test_tool_from_function_uses_name_kwarg(self) -> None:
        code = (
            "from langchain_core.tools import Tool\n\n"
            "search_tool = Tool.from_function(\n"
            "    func=lambda q: q,\n"
            "    name='search',\n"
            "    description='Search the web',\n"
            ")\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        assert any(t.display_name == "search" for t in tools)

    def test_factory_function_returning_structured_tool_named_after_factory(self) -> None:
        """A factory function that builds the tool's name from its own
        parameters (not a string literal) can't have its name resolved
        statically — fall back to the enclosing function name, flagged
        dynamic_name=True, rather than silently dropping the tool."""
        code = (
            "from langchain_core.tools import StructuredTool\n\n"
            "def create_live_api_tool(name, description, api_url):\n"
            "    def _call(**kwargs):\n"
            "        return requests.get(api_url, params=kwargs)\n"
            "    return StructuredTool.from_function(\n"
            "        func=_call,\n"
            "        name=name,\n"
            "        description=description,\n"
            "    )\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        matches = [t for t in tools if t.display_name == "create_live_api_tool"]
        assert matches, f"expected a TOOL named after the factory function, got {[t.display_name for t in tools]}"
        assert matches[0].metadata.get("dynamic_name") is True


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
