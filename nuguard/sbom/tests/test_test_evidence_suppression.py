"""Integration coverage for central test-file SBOM evidence suppression."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.types import ComponentType


_CONFIG = AiSbomConfig(include_extensions={".py", ".sh"}, enable_llm=False)
_RUNTIME_TYPES = {
    ComponentType.AGENT,
    ComponentType.GUARDRAIL,
    ComponentType.MCP_SERVER,
    ComponentType.MODEL,
    ComponentType.TOOL,
    ComponentType.PROMPT,
    ComponentType.FRAMEWORK,
}
_SOURCE = """\
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.types import interrupt
from mcp import ClientSession

@tool
def lookup(query: str) -> str:
    return query

model = ChatOpenAI(model="gpt-4o")
agent = create_react_agent(model, [lookup])
approval = interrupt("Approve the proposed action")
policy = SystemMessage(
    content="You are a careful production assistant. Use tools only when needed."
)
"""


def _extract(root: Path):
    return AiSbomExtractor().extract_from_path(root, _CONFIG)


def test_test_only_runtime_nodes_are_suppressed_but_deployment_is_retained(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_agent.py").write_text(_SOURCE, encoding="utf-8")
    (tests / "deploy.sh").write_text(
        "#!/bin/sh\nkubectl apply -f deployment.yaml\n",
        encoding="utf-8",
    )

    doc = _extract(tmp_path)

    assert not [node for node in doc.nodes if node.component_type in _RUNTIME_TYPES]
    assert any(node.component_type == ComponentType.DEPLOYMENT for node in doc.nodes)


@pytest.mark.parametrize("filename", ["test_agent.py", "agent_test.py"])
def test_root_pytest_filename_is_suppressed(tmp_path: Path, filename: str) -> None:
    (tmp_path / filename).write_text(_SOURCE, encoding="utf-8")

    doc = _extract(tmp_path)

    assert not [node for node in doc.nodes if node.component_type in _RUNTIME_TYPES]


def test_test_only_mcp_server_does_not_synthesize_fallback_agent(tmp_path: Path) -> None:
    (tmp_path / "test_mcp_client.py").write_text(
        "from mcp import ClientSession\n",
        encoding="utf-8",
    )

    doc = _extract(tmp_path)

    assert not any(
        node.component_type in {ComponentType.MCP_SERVER, ComponentType.AGENT}
        for node in doc.nodes
    )


def test_production_node_survives_without_same_name_test_evidence(tmp_path: Path) -> None:
    source = tmp_path / "src"
    tests = tmp_path / "tests"
    source.mkdir()
    tests.mkdir()
    (source / "agent.py").write_text(_SOURCE, encoding="utf-8")
    (tests / "test_agent.py").write_text(_SOURCE, encoding="utf-8")

    doc = _extract(tmp_path)
    runtime_nodes = [node for node in doc.nodes if node.component_type in _RUNTIME_TYPES]

    assert {node.component_type for node in runtime_nodes} == _RUNTIME_TYPES
    assert all(
        evidence.location.path == "src/agent.py"
        for node in runtime_nodes
        for evidence in node.evidence
    )


def test_scanning_fixture_root_directly_preserves_runtime_nodes(tmp_path: Path) -> None:
    fixture_root = tmp_path / "tests" / "fixtures" / "sample_agent"
    fixture_root.mkdir(parents=True)
    (fixture_root / "agent.py").write_text(_SOURCE, encoding="utf-8")

    doc = _extract(fixture_root)

    observed_types = {
        node.component_type for node in doc.nodes if node.component_type in _RUNTIME_TYPES
    }
    assert observed_types == _RUNTIME_TYPES
    assert all(
        evidence.location.path == "agent.py"
        for node in doc.nodes
        if node.component_type in _RUNTIME_TYPES
        for evidence in node.evidence
    )
