"""Tests for merge_sniffed_endpoints_into_sbom (Phase 2 — browser-based
endpoint discovery, merge/dedup half)."""
from __future__ import annotations

from nuguard.common.browser_login.endpoint_sniffer import (
    BROWSER_SNIFF_EVIDENCE_KIND,
    SniffedRequest,
    merge_sniffed_endpoints_into_sbom,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType


def _endpoint_node(method: str, path: str, name: str | None = None) -> Node:
    return Node(
        name=name or f"{method} {path}",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(
            endpoint=path,
            method=method,
            extras={"canonical_name": f"endpoint:{method.upper()}:{path}"},
        ),
    )


def test_no_sniffed_endpoints_is_noop() -> None:
    doc = AiSbomDocument(target=".", nodes=[_endpoint_node("GET", "/api/users")])

    added = merge_sniffed_endpoints_into_sbom(doc, [])

    assert added == 0
    assert len(doc.nodes) == 1


def test_sniffed_endpoint_deduped_against_existing_node() -> None:
    doc = AiSbomDocument(target=".", nodes=[_endpoint_node("GET", "/api/users")])
    sniffed = [SniffedRequest(method="GET", url="http://t/api/users", path="/api/users")]

    added = merge_sniffed_endpoints_into_sbom(doc, sniffed)

    assert added == 0
    assert len(doc.nodes) == 1


def test_new_sniffed_endpoint_creates_node_with_dynamic_evidence() -> None:
    doc = AiSbomDocument(target=".", nodes=[_endpoint_node("GET", "/api/users")])
    sniffed = [SniffedRequest(method="POST", url="http://t/api/orders", path="/api/orders")]

    added = merge_sniffed_endpoints_into_sbom(doc, sniffed)

    assert added == 1
    new_nodes = [n for n in doc.nodes if n.metadata.endpoint == "/api/orders"]
    assert len(new_nodes) == 1
    node = new_nodes[0]
    assert node.metadata.method == "POST"
    assert node.evidence[0].kind == BROWSER_SNIFF_EVIDENCE_KIND
