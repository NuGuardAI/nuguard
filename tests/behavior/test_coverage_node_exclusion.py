"""Tests for NON_EXERCISABLE_NODE_TYPES exclusion in BehaviorRunner and CoverageState.

Validates that infrastructure, always-on runtime, and abstract construct node types
are excluded from coverage probe targets, while AGENT and TOOL nodes are retained.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from nuguard.behavior.coverage import NON_EXERCISABLE_NODE_TYPES
from nuguard.behavior.runner import BehaviorRunner
from nuguard.config import BehaviorConfig
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, NodeType

_NS = uuid.NAMESPACE_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(name: str, node_type: NodeType) -> Node:
    nid = uuid.uuid5(_NS, f"{node_type.value}/{name}")
    return Node(
        id=nid,
        name=name,
        component_type=node_type,
        confidence=0.9,
        metadata=NodeMetadata(description=f"{name} description"),
    )


def _runner_from_sbom(sbom: AiSbomDocument) -> BehaviorRunner:
    return BehaviorRunner(
        config=BehaviorConfig(target="http://localhost:9999"),
        sbom=sbom,
    )


# ---------------------------------------------------------------------------
# NON_EXERCISABLE_NODE_TYPES constant membership
# ---------------------------------------------------------------------------

class TestNonExercisableNodeTypesMembership:
    """The constant must contain exactly the expected infrastructure/runtime/abstract types."""

    def test_infrastructure_types_are_excluded(self) -> None:
        assert "AUTH" in NON_EXERCISABLE_NODE_TYPES
        assert "IAM" in NON_EXERCISABLE_NODE_TYPES
        assert "DEPLOYMENT" in NON_EXERCISABLE_NODE_TYPES
        assert "CONTAINER_IMAGE" in NON_EXERCISABLE_NODE_TYPES

    def test_always_on_runtime_types_are_excluded(self) -> None:
        assert "FRAMEWORK" in NON_EXERCISABLE_NODE_TYPES
        assert "MODEL" in NON_EXERCISABLE_NODE_TYPES

    def test_abstract_construct_types_are_excluded(self) -> None:
        assert "PRIVILEGE" in NON_EXERCISABLE_NODE_TYPES
        assert "DATASTORE" in NON_EXERCISABLE_NODE_TYPES
        assert "PROMPT" in NON_EXERCISABLE_NODE_TYPES
        assert "API_ENDPOINT" in NON_EXERCISABLE_NODE_TYPES

    def test_exercisable_types_not_excluded(self) -> None:
        assert "AGENT" not in NON_EXERCISABLE_NODE_TYPES
        assert "TOOL" not in NON_EXERCISABLE_NODE_TYPES
        assert "GUARDRAIL" not in NON_EXERCISABLE_NODE_TYPES

    def test_is_frozenset(self) -> None:
        assert isinstance(NON_EXERCISABLE_NODE_TYPES, frozenset)

    def test_all_values_are_uppercase(self) -> None:
        for entry in NON_EXERCISABLE_NODE_TYPES:
            assert entry == entry.upper(), f"Expected uppercase: {entry!r}"


# ---------------------------------------------------------------------------
# BehaviorRunner.__init__ — SBOM node filtering
# ---------------------------------------------------------------------------

class TestBehaviorRunnerNodeFiltering:
    """BehaviorRunner must exclude non-exercisable nodes from agent/tool lists."""

    def test_agent_nodes_are_included(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("MyAgent", NodeType.AGENT)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "MyAgent" in runner._agent_names

    def test_tool_nodes_are_included(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("search_tool", NodeType.TOOL)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "search_tool" in runner._tool_names

    def test_model_nodes_are_excluded(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("gpt-4o", NodeType.MODEL)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "gpt-4o" not in runner._agent_names
        assert "gpt-4o" not in runner._tool_names
        assert "gpt-4o" not in runner._component_descriptions

    def test_framework_nodes_are_excluded(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("LangChain", NodeType.FRAMEWORK)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "LangChain" not in runner._agent_names
        assert "LangChain" not in runner._tool_names
        assert "LangChain" not in runner._component_descriptions

    def test_auth_nodes_are_excluded(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("OAuth2", NodeType.AUTH)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "OAuth2" not in runner._component_descriptions

    def test_datastore_nodes_are_excluded(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("PostgresDB", NodeType.DATASTORE)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "PostgresDB" not in runner._component_descriptions

    def test_deployment_nodes_are_excluded(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("k8s-deployment", NodeType.DEPLOYMENT)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "k8s-deployment" not in runner._component_descriptions

    def test_prompt_nodes_are_excluded(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_node("system-prompt", NodeType.PROMPT)],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)
        assert "system-prompt" not in runner._component_descriptions

    def test_mixed_sbom_only_exercisable_nodes_retained(self) -> None:
        """Mixed SBOM: only AGENT and TOOL nodes should appear in coverage targets."""
        sbom = AiSbomDocument(
            target="./app",
            nodes=[
                _node("MyAgent", NodeType.AGENT),
                _node("search_tool", NodeType.TOOL),
                _node("gpt-4o", NodeType.MODEL),
                _node("LangChain", NodeType.FRAMEWORK),
                _node("OAuth2", NodeType.AUTH),
                _node("PostgresDB", NodeType.DATASTORE),
                _node("k8s-pod", NodeType.DEPLOYMENT),
                _node("ecr-image", NodeType.CONTAINER_IMAGE),
                _node("admin-role", NodeType.PRIVILEGE),
                _node("system-prompt", NodeType.PROMPT),
                _node("/chat", NodeType.API_ENDPOINT),
                _node("iam-role", NodeType.IAM),
            ],
            edges=[],
        )
        runner = _runner_from_sbom(sbom)

        assert runner._agent_names == ["MyAgent"]
        assert runner._tool_names == ["search_tool"]
        assert set(runner._component_descriptions.keys()) == {"MyAgent", "search_tool"}

    def test_no_sbom_yields_empty_coverage(self) -> None:
        runner = BehaviorRunner(
            config=BehaviorConfig(target="http://localhost:9999"),
            sbom=None,
        )
        assert runner._agent_names == []
        assert runner._tool_names == []
        assert runner._component_descriptions == {}

    def test_all_non_exercisable_types_are_filtered(self) -> None:
        """Every type in NON_EXERCISABLE_NODE_TYPES must produce no coverage entry."""
        type_map = {t.value: t for t in NodeType}
        nodes = []
        for type_str in NON_EXERCISABLE_NODE_TYPES:
            if type_str in type_map:
                nodes.append(_node(f"node_{type_str}", type_map[type_str]))

        sbom = AiSbomDocument(target="./app", nodes=nodes, edges=[])
        runner = _runner_from_sbom(sbom)

        assert runner._agent_names == []
        assert runner._tool_names == []
        assert runner._component_descriptions == {}
