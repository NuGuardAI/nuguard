"""Tests for nuguard.sbom.llm_client enrichment helpers."""

from __future__ import annotations

import json

import pytest

from nuguard.sbom.llm_client import _DESCRIPTION_BATCH_SIZE, enrich_node_descriptions
from nuguard.sbom.models import ComponentType, Node, NodeMetadata


class _FakeLLMClient:
    """Records prompts and returns a canned structured JSON response per call."""

    def __init__(self) -> None:
        self.call_count = 0
        self.prompts: list[str] = []

    async def complete(self, prompt: str, system: str) -> str:
        self.call_count += 1
        self.prompts.append(prompt)
        start = prompt.find("[")
        end = prompt.rfind("]") + 1
        items = json.loads(prompt[start:end])
        return json.dumps({item["id"]: f"Description for {item['name']}" for item in items})


def _make_tool_node(name: str) -> Node:
    return Node(
        name=name,
        component_type=ComponentType.TOOL,
        confidence=0.8,
        metadata=NodeMetadata(),
    )


@pytest.mark.asyncio
async def test_enrich_node_descriptions_batches_instead_of_one_call_per_node() -> None:
    nodes = [_make_tool_node(f"Tool {i}") for i in range(60)]
    client = _FakeLLMClient()

    await enrich_node_descriptions(nodes, client)

    # 60 nodes at batch size _DESCRIPTION_BATCH_SIZE should take a handful of
    # calls, never one call per node.
    expected_batches = -(-60 // _DESCRIPTION_BATCH_SIZE)  # ceil division
    assert client.call_count == expected_batches
    assert client.call_count < len(nodes)
    for node in nodes:
        assert node.metadata.description == f"Description for {node.name}"


@pytest.mark.asyncio
async def test_enrich_node_descriptions_skips_nodes_with_existing_description() -> None:
    described = _make_tool_node("Already Described")
    described.metadata.description = "This tool already has a sufficiently long description."
    undescribed = _make_tool_node("Needs Description")
    client = _FakeLLMClient()

    await enrich_node_descriptions([described, undescribed], client)

    assert client.call_count == 1
    assert described.metadata.description == "This tool already has a sufficiently long description."
    assert undescribed.metadata.description == "Description for Needs Description"


@pytest.mark.asyncio
async def test_enrich_node_descriptions_ignores_non_agent_tool_nodes() -> None:
    other = Node(
        name="Some Model",
        component_type=ComponentType.MODEL,
        confidence=0.8,
        metadata=NodeMetadata(),
    )
    client = _FakeLLMClient()

    await enrich_node_descriptions([other], client)

    assert client.call_count == 0
    assert other.metadata.description in (None, "")


@pytest.mark.asyncio
async def test_enrich_node_descriptions_no_targets_makes_no_calls() -> None:
    client = _FakeLLMClient()
    await enrich_node_descriptions([], client)
    assert client.call_count == 0
