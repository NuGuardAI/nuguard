from __future__ import annotations

import asyncio
from unittest.mock import patch

from nuguard.common.auto_sbom_enricher import (
    _infer_required_field_from_error,
    maybe_auto_enrich_sbom,
)
from nuguard.sbom.models import AiSbomDocument, Edge, Node, ScanSummary
from nuguard.sbom.types import ComponentType, RelationshipType


def _build_low_confidence_sbom() -> AiSbomDocument:
    model = Node(name="gpt-4o-mini", component_type=ComponentType.MODEL, confidence=0.9)
    tool = Node(name="generic-tool", component_type=ComponentType.TOOL, confidence=0.8)
    datastore = Node(
        name="postgres",
        component_type=ComponentType.DATASTORE,
        confidence=0.9,
        metadata={"classified_fields": {"users": ["email", "name"]}},
    )
    api_chat = Node(name="Chat API", component_type=ComponentType.API_ENDPOINT, confidence=0.7)
    api_health = Node(name="Health API", component_type=ComponentType.API_ENDPOINT, confidence=0.7)

    return AiSbomDocument(
        target="stateset-icommerce",
        nodes=[model, tool, datastore, api_chat, api_health],
        edges=[],
        summary=ScanSummary(api_endpoints=["/chat/message", "/health", "/api/orders/{order_id}"]),
    )


def _build_high_confidence_sbom() -> AiSbomDocument:
    agent = Node(name="Commerce Assistant", component_type=ComponentType.AGENT, confidence=0.9)
    model = Node(name="gpt-4o-mini", component_type=ComponentType.MODEL, confidence=0.9)
    tool_a = Node(name="fetch-tool", component_type=ComponentType.TOOL, confidence=0.9)
    tool_b = Node(name="write-tool", component_type=ComponentType.TOOL, confidence=0.9)
    datastore = Node(
        name="postgres",
        component_type=ComponentType.DATASTORE,
        confidence=0.9,
        metadata={"pii_fields": ["email"], "phi_fields": []},
    )
    api_a = Node(
        name="Chat API",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata={"endpoint": "/chat/message", "method": "POST"},
    )
    api_b = Node(
        name="Health API",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata={"endpoint": "/health", "method": "GET"},
    )
    api_c = Node(
        name="Orders API",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata={"endpoint": "/api/orders/{order_id}", "method": "PATCH"},
    )

    edges = [
        Edge(source=agent.id, target=model.id, relationship_type=RelationshipType.USES),
        Edge(source=agent.id, target=tool_a.id, relationship_type=RelationshipType.CALLS),
        Edge(source=agent.id, target=tool_b.id, relationship_type=RelationshipType.CALLS),
        Edge(source=tool_b.id, target=datastore.id, relationship_type=RelationshipType.ACCESSES),
    ]

    return AiSbomDocument(
        target="stateset-icommerce",
        nodes=[agent, model, tool_a, tool_b, datastore, api_a, api_b, api_c],
        edges=edges,
        summary=ScanSummary(api_endpoints=["/chat/message", "/health", "/api/orders/{order_id}"]),
    )


def test_common_auto_enriches_low_confidence_sbom_without_probe() -> None:
    sbom = _build_low_confidence_sbom()

    result = asyncio.run(
        maybe_auto_enrich_sbom(
            sbom=sbom,
            sbom_path=None,
            target_url=None,
        )
    )

    assert result.enriched is True
    assert result.probe_attempted is False
    assert result.confidence_after >= result.confidence_before

    component_types = {node.component_type for node in result.sbom.nodes}
    assert ComponentType.AGENT in component_types


def test_common_auto_skips_high_confidence_sbom() -> None:
    sbom = _build_high_confidence_sbom()

    result = asyncio.run(
        maybe_auto_enrich_sbom(
            sbom=sbom,
            sbom_path=None,
            target_url=None,
        )
    )

    assert result.enriched is False
    assert result.confidence_before >= 0.65
    assert result.confidence_after == result.confidence_before


def test_common_auto_writes_enriched_artifact_when_sbom_path_present(tmp_path) -> None:
    sbom = _build_low_confidence_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text("{}", encoding="utf-8")

    result = asyncio.run(
        maybe_auto_enrich_sbom(
            sbom=sbom,
            sbom_path=sbom_path,
            target_url=None,
        )
    )

    assert result.enriched is True
    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    assert result.artifact_path.name == "app.sbom.enriched.json"


def test_common_infer_required_field_from_error() -> None:
    assert _infer_required_field_from_error('{"error":"Missing \\"text\\" field."}') == "text"
    assert _infer_required_field_from_error('{"message":"Missing \\"query\\" field."}') == "query"
    assert _infer_required_field_from_error("Missing 'prompt' field") == "prompt"
    assert _infer_required_field_from_error("some unrelated error") is None


def test_common_infer_required_field_from_fastapi_detail_loc() -> None:
    payload = (
        '{"detail":[{"type":"missing","loc":["body","user_query"],'
        '"msg":"Field required","input":{"message":"hello"}}]}'
    )
    assert _infer_required_field_from_error(payload) == "user_query"


def test_common_infer_required_field_from_pydantic_v1_style_detail() -> None:
    payload = (
        '{"detail":[{"loc":["body","phrases"],"msg":"field required",'
        '"type":"value_error.missing"}]}'
    )
    assert _infer_required_field_from_error(payload) == "phrases"


def test_common_copies_description_from_source_extras_when_missing() -> None:
    sbom = _build_high_confidence_sbom()
    tool_node = next(n for n in sbom.nodes if n.component_type == ComponentType.TOOL)
    tool_node.metadata.description = None
    tool_node.metadata.extras = {"description": "Fetches order data from the commerce backend."}

    result = asyncio.run(
        maybe_auto_enrich_sbom(
            sbom=sbom,
            sbom_path=None,
            target_url=None,
            llm_enabled=False,
        )
    )

    updated_tool = next(n for n in result.sbom.nodes if n.id == tool_node.id)
    assert updated_tool.metadata.description == "Fetches order data from the commerce backend."
    assert updated_tool.metadata.extras is not None
    assert updated_tool.metadata.extras.get("description_source") == "original_sbom"


def test_common_generates_description_with_llm_when_enabled() -> None:
    sbom = _build_high_confidence_sbom()
    tool_node = next(n for n in sbom.nodes if n.component_type == ComponentType.TOOL)
    tool_node.metadata.description = None
    tool_node.metadata.extras = {}

    class _FakeLLMClient:
        def __init__(self, model=None, api_key=None, api_base=None, **kwargs):
            self.model = model
            self.api_key = api_key

        async def complete(self, prompt: str, system: str | None = None, label: str = "", **kwargs):
            return "Retrieves catalog and order details for assistant responses."

    with patch("nuguard.common.llm_client.LLMClient", _FakeLLMClient):
        result = asyncio.run(
            maybe_auto_enrich_sbom(
                sbom=sbom,
                sbom_path=None,
                target_url=None,
                llm_enabled=True,
                llm_model="openai/gpt-4.1-mini",
            )
        )

    updated_tool = next(n for n in result.sbom.nodes if n.id == tool_node.id)
    assert updated_tool.metadata.description == "Retrieves catalog and order details for assistant responses."
    assert updated_tool.metadata.extras is not None
    assert updated_tool.metadata.extras.get("description_source") == "llm_generated"