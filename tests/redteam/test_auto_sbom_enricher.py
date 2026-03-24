from __future__ import annotations

import asyncio

from nuguard.redteam.enrichment.auto_enricher import (
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


def test_auto_enriches_low_confidence_sbom_without_target_probe() -> None:
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

    ds_nodes = [n for n in result.sbom.nodes if n.component_type == ComponentType.DATASTORE]
    assert ds_nodes
    assert ds_nodes[0].metadata.pii_fields == ["email", "name"]


def test_auto_skips_enrichment_for_high_confidence_sbom() -> None:
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


def test_infer_required_field_from_error() -> None:
    assert _infer_required_field_from_error('{"error":"Missing \\"text\\" field."}') == "text"
    assert _infer_required_field_from_error('{"message":"Missing \\"query\\" field."}') == "query"
    assert _infer_required_field_from_error("Missing 'prompt' field") == "prompt"
    assert _infer_required_field_from_error("some unrelated error") is None
