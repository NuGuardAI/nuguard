"""Tests for _resolve_edges() in AiSbomExtractor.

Covers:
  TestHintCanonicalization  — colon-canonical hints resolve to Edge objects
  TestAgentModelHints       - only hinted agents receive model dependencies
  TestFrameworkAgentFallback — FRAMEWORK→AGENT CALLS via metadata.framework
  TestFrameworkToolFallback  — FRAMEWORK→TOOL CALLS via metadata.framework
  TestAgentDatastoreTransitive — AGENT→DATASTORE via CALLS→TOOL→ACCESSES chain
  TestLangGraphFixture       — end-to-end extraction on the langgraph_research_agent fixture
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument, Evidence, Node, SourceLocation
from nuguard.sbom.tests.conftest import FIXTURES, extract, nodes
from nuguard.sbom.types import ComponentType, RelationshipType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LANGGRAPH_FIXTURE = FIXTURES / "langgraph_research_agent"


def _edges_by_rel(doc, rel: RelationshipType):
    return [e for e in doc.edges if e.relationship_type == rel]


def _node_ids(doc, ctype: ComponentType) -> set:
    return {n.id for n in doc.nodes if n.component_type == ctype}


def _located_node(name: str, kind: ComponentType, path: str) -> Node:
    return Node(
        name=name,
        component_type=kind,
        confidence=0.9,
        evidence=[
            Evidence(
                kind="ast",
                confidence=0.9,
                detail="fixture",
                location=SourceLocation(path=path, line=1),
            )
        ],
    )


class TestScopedComponentEdges:
    def test_prompt_datastore_and_guardrail_share_an_agent(self) -> None:
        agent = _located_node("Review Agent", ComponentType.AGENT, "app/agent.py")
        prompt = _located_node("Review Instructions", ComponentType.PROMPT, "app/agent.py")
        datastore = _located_node("Orders DB", ComponentType.DATASTORE, "app/agent.py")
        guardrail = _located_node("Input Filter", ComponentType.GUARDRAIL, "app/agent.py")
        doc = AiSbomDocument(target="fixture", nodes=[agent, prompt, datastore, guardrail])

        AiSbomExtractor()._resolve_edges(doc, {})

        actual = {(e.source, e.target, e.relationship_type) for e in doc.edges}
        assert (agent.id, prompt.id, RelationshipType.USES) in actual
        assert (agent.id, datastore.id, RelationshipType.ACCESSES) in actual
        assert (guardrail.id, prompt.id, RelationshipType.PROTECTS) in actual
        assert all(e.derivation == "fallback_heuristic" for e in doc.edges)
        assert AiSbomDocument.model_validate(doc.model_dump(mode="json")).edges == doc.edges

    def test_ambiguous_and_unrelated_components_are_not_cross_linked(self) -> None:
        first = _located_node("Researcher", ComponentType.AGENT, "app/agents.py")
        second = _located_node("Writer", ComponentType.AGENT, "app/agents.py")
        named_prompt = _located_node("Writer Instructions", ComponentType.PROMPT, "app/agents.py")
        shared_datastore = _located_node("Shared DB", ComponentType.DATASTORE, "app/agents.py")
        other_prompt = _located_node("Other Prompt", ComponentType.PROMPT, "app/prompts.py")
        other_datastore = _located_node("Other DB", ComponentType.DATASTORE, "app/db.py")
        doc = AiSbomDocument(
            target="fixture",
            nodes=[first, second, named_prompt, shared_datastore, other_prompt, other_datastore],
        )

        AiSbomExtractor()._resolve_edges(doc, {})

        agent_targets = {
            (e.source, e.target)
            for e in doc.edges
            if e.source in {first.id, second.id}
            and e.relationship_type in {RelationshipType.USES, RelationshipType.ACCESSES}
        }
        assert (second.id, named_prompt.id) in agent_targets
        assert (first.id, named_prompt.id) not in agent_targets
        assert not any(
            target in {shared_datastore.id, other_prompt.id, other_datastore.id}
            for _, target in agent_targets
        )


# ---------------------------------------------------------------------------
# LangGraph research agent fixture — broad integration checks
# ---------------------------------------------------------------------------


class TestLangGraphFixture:
    """End-to-end edge quality tests on the langgraph_research_agent fixture."""

    @pytest.fixture(scope="class")
    def doc(self):
        return extract(_LANGGRAPH_FIXTURE)

    def test_framework_node_present(self, doc) -> None:
        fw = nodes(doc, ComponentType.FRAMEWORK)
        assert fw, "Expected at least one FRAMEWORK node"

    def test_agent_nodes_present(self, doc) -> None:
        agents = nodes(doc, ComponentType.AGENT)
        assert agents, "Expected AGENT nodes from add_node calls"

    def test_model_node_present(self, doc) -> None:
        models = nodes(doc, ComponentType.MODEL)
        assert models, "Expected MODEL node (ChatAnthropic)"

    def test_tool_nodes_present(self, doc) -> None:
        tools = nodes(doc, ComponentType.TOOL)
        assert tools, "Expected TOOL nodes from @tool decorator / ToolNode"

    def test_agent_uses_model_edges(self, doc) -> None:
        uses_edges = _edges_by_rel(doc, RelationshipType.USES)
        model_ids = _node_ids(doc, ComponentType.MODEL)
        agent_to_model = [e for e in uses_edges if e.target in model_ids]
        assert agent_to_model, "Expected AGENT -[USES]-> MODEL edges"

    def test_agent_tool_edges_are_not_repository_fallbacks(self, doc) -> None:
        agent_ids = _node_ids(doc, ComponentType.AGENT)
        tool_ids = _node_ids(doc, ComponentType.TOOL)
        assert tool_ids
        agent_to_tool = [
            edge for edge in _edges_by_rel(doc, RelationshipType.CALLS)
            if edge.source in agent_ids and edge.target in tool_ids
        ]
        assert all(edge.derivation == "hint" for edge in agent_to_tool)

    def test_all_edge_nodes_exist(self, doc) -> None:
        all_ids = {n.id for n in doc.nodes}
        for edge in doc.edges:
            assert edge.source in all_ids, f"Edge source {edge.source} not in nodes"
            assert edge.target in all_ids, f"Edge target {edge.target} not in nodes"

    def test_no_duplicate_edges(self, doc) -> None:
        seen: set[tuple] = set()
        for e in doc.edges:
            key = (e.source, e.target, e.relationship_type)
            assert key not in seen, f"Duplicate edge: {key}"
            seen.add(key)

    def test_langgraph_framework_confidence(self, doc) -> None:
        fw = nodes(doc, ComponentType.FRAMEWORK)
        langgraph_nodes = [
            f for f in fw
            if "langgraph" in (f.metadata.extras.get("adapter", "") or "")
            or "langgraph" in f.name.lower()
        ]
        assert langgraph_nodes, "Expected framework:langgraph node"
        assert langgraph_nodes[0].confidence >= 0.88


# ---------------------------------------------------------------------------
# FRAMEWORK → AGENT CALLS via metadata.framework fallback
# ---------------------------------------------------------------------------


class TestFrameworkAgentFallback:
    """Framework→Agent CALLS edges are added via shared metadata.framework."""

    @pytest.fixture(scope="class")
    def doc(self):
        return extract(_LANGGRAPH_FIXTURE)

    def test_framework_calls_agent(self, doc) -> None:
        calls_edges = _edges_by_rel(doc, RelationshipType.CALLS)
        fw_ids = _node_ids(doc, ComponentType.FRAMEWORK)
        agent_ids = _node_ids(doc, ComponentType.AGENT)
        fw_to_agent = [e for e in calls_edges if e.source in fw_ids and e.target in agent_ids]
        assert fw_to_agent, "Expected FRAMEWORK -[CALLS]-> AGENT edges"

    def test_framework_calls_agent_tagged_fallback_heuristic(self, doc) -> None:
        """FRAMEWORK→AGENT is a metadata-string-match guess, not code evidence —
        must be tagged derivation="fallback_heuristic" with a confidence score."""
        calls_edges = _edges_by_rel(doc, RelationshipType.CALLS)
        fw_ids = _node_ids(doc, ComponentType.FRAMEWORK)
        agent_ids = _node_ids(doc, ComponentType.AGENT)
        fw_to_agent = [e for e in calls_edges if e.source in fw_ids and e.target in agent_ids]
        assert fw_to_agent
        for edge in fw_to_agent:
            assert edge.derivation == "fallback_heuristic"
            assert edge.confidence is not None


# ---------------------------------------------------------------------------
# AGENT -> MODEL relationships come from adapter hints, not missing-edge filling.
# ---------------------------------------------------------------------------


class TestAgentModelHints:
    def test_only_hinted_langgraph_agents_receive_model_edges(self) -> None:
        doc = extract(_LANGGRAPH_FIXTURE)
        agent_ids = _node_ids(doc, ComponentType.AGENT)
        model_ids = _node_ids(doc, ComponentType.MODEL)
        agent_to_model = [
            edge for edge in _edges_by_rel(doc, RelationshipType.USES)
            if edge.source in agent_ids and edge.target in model_ids
        ]
        by_id = {node.id: node for node in doc.nodes}
        assert {by_id[edge.source].name for edge in agent_to_model} == {"Researcher", "Writer"}
        assert all(edge.derivation == "hint" and edge.confidence is None for edge in agent_to_model)


# ---------------------------------------------------------------------------
# AGENT → DATASTORE ACCESSES transitive via CALLS chain
# ---------------------------------------------------------------------------


class TestAgentDatastoreTransitive:
    """
    AGENT→DATASTORE ACCESSES is inferred when AGENT-[CALLS]->TOOL and
    TOOL-[ACCESSES]->DATASTORE edges both exist.

    Uses the apps/customer_service_bot fixture which has agents, tools, and
    triggers the Python datastore adapter (if datastores are present) or
    falls back to verifying the transitive logic with whatever fixture has
    explicit ACCESSES edges.
    """

    @pytest.fixture(scope="class")
    def doc(self):
        apps = Path(__file__).parent / "fixtures" / "apps"
        # Use the first available app that may have datastores, else langgraph agent
        cs_bot = apps / "customer_service_bot"
        if cs_bot.exists():
            return extract(cs_bot)
        return extract(_LANGGRAPH_FIXTURE)

    def test_no_dangling_accesses_edges(self, doc) -> None:
        """Any ACCESSES edge must reference existing node IDs."""
        all_ids = {n.id for n in doc.nodes}
        for edge in _edges_by_rel(doc, RelationshipType.ACCESSES):
            assert edge.source in all_ids
            assert edge.target in all_ids

    def test_transitive_agent_datastore_if_chain_exists(self, doc) -> None:
        """If TOOL→DATASTORE ACCESSES and AGENT→TOOL CALLS both exist, then
        AGENT→DATASTORE ACCESSES should also exist (transitive inference)."""
        calls_edges = _edges_by_rel(doc, RelationshipType.CALLS)
        accesses_edges = _edges_by_rel(doc, RelationshipType.ACCESSES)
        tool_ids = _node_ids(doc, ComponentType.TOOL)
        ds_ids = _node_ids(doc, ComponentType.DATASTORE)

        tool_to_ds = {
            e.source: e.target for e in accesses_edges if e.source in tool_ids and e.target in ds_ids
        }
        if not tool_to_ds:
            pytest.skip("No TOOL→DATASTORE ACCESSES edges — transitive check not applicable")

        agent_ids = _node_ids(doc, ComponentType.AGENT)
        agents_calling_tools_with_ds = {
            e.source for e in calls_edges
            if e.source in agent_ids and e.target in tool_to_ds
        }
        if not agents_calling_tools_with_ds:
            pytest.skip("No AGENT→TOOL CALLS chains found — transitive check not applicable")

        agent_ds_accesses = {
            e.source for e in accesses_edges
            if e.source in agent_ids and e.target in ds_ids
        }
        assert agents_calling_tools_with_ds <= agent_ds_accesses, (
            "Expected transitive AGENT→DATASTORE ACCESSES for all agents that "
            "CALLS a tool that ACCESSES a datastore"
        )


# ---------------------------------------------------------------------------
# Hint canonicalization — colon names must resolve
# ---------------------------------------------------------------------------


class TestHintCanonicalization:
    """
    The LangGraph adapter emits RelationshipHints with canonical names like
    'framework:langgraph' (colons) while the node lookup uses canonicalized
    names like 'framework_langgraph' (underscores). Both must resolve.

    We test this indirectly: if the fixture has MODEL nodes linked from any
    explicit hint, the USES edges must appear in the document rather than
    being silently dropped.
    """

    @pytest.fixture(scope="class")
    def doc(self):
        return extract(_LANGGRAPH_FIXTURE)

    def test_uses_edges_not_empty(self, doc) -> None:
        """At minimum, USES edges should exist — if all hints were dropped this fails."""
        uses = _edges_by_rel(doc, RelationshipType.USES)
        assert uses, "Expected USES edges; hint canonicalization failure would drop them"

    def test_calls_edges_not_empty(self, doc) -> None:
        """At minimum, CALLS edges should exist — hint drops would eliminate these."""
        calls = _edges_by_rel(doc, RelationshipType.CALLS)
        assert calls, "Expected CALLS edges; hint canonicalization failure would drop them"


# ---------------------------------------------------------------------------
# Edge derivation/confidence tagging
# ---------------------------------------------------------------------------


class TestEdgeDerivation:
    """Every edge must be tagged with a derivation, and the two tiers must be
    mutually consistent: "hint" edges (real code evidence) carry no separate
    confidence score, while "fallback_heuristic" edges (structural guesses)
    always carry one."""

    @pytest.fixture(scope="class")
    def doc(self):
        return extract(_LANGGRAPH_FIXTURE)

    def test_all_edges_have_a_valid_derivation(self, doc) -> None:
        for edge in doc.edges:
            assert edge.derivation in ("hint", "fallback_heuristic")

    def test_hint_edges_have_no_confidence(self, doc) -> None:
        for edge in doc.edges:
            if edge.derivation == "hint":
                assert edge.confidence is None

    def test_fallback_heuristic_edges_have_confidence(self, doc) -> None:
        for edge in doc.edges:
            if edge.derivation == "fallback_heuristic":
                assert edge.confidence is not None

    def test_some_hint_edges_present(self, doc) -> None:
        """The langgraph fixture's adapter emits explicit RelationshipHints —
        at least one edge should be evidence-backed, not just fallback guesses."""
        assert any(e.derivation == "hint" for e in doc.edges)
