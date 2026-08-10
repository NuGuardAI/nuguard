"""Unit tests for nuguard.sbom.enricher, focused on API_ENDPOINT auth_required derivation."""

from __future__ import annotations

from nuguard.sbom.enricher import enrich
from nuguard.sbom.models import AiSbomDocument, Edge, Node
from nuguard.sbom.types import ComponentType, RelationshipType


def _endpoint(name: str = "get-user", **meta_kwargs: object) -> Node:
    return Node(
        name=name,
        component_type=ComponentType.API_ENDPOINT,
        confidence=1.0,
        metadata=meta_kwargs,  # type: ignore[arg-type]
    )


def _auth(name: str = "auth") -> Node:
    return Node(name=name, component_type=ComponentType.AUTH, confidence=1.0)


class TestApiEndpointAuthRequired:
    def test_auth_required_true_when_protected_by_auth_edge(self) -> None:
        auth_node = _auth()
        endpoint = _endpoint()
        edge = Edge(
            source=auth_node.id,
            target=endpoint.id,
            relationship_type=RelationshipType.PROTECTS,
        )
        doc = AiSbomDocument(target="t", nodes=[auth_node, endpoint], edges=[edge])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.auth_required is True

    def test_auth_required_false_when_no_protecting_auth_node(self) -> None:
        endpoint = _endpoint()
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.auth_required is False

    def test_auth_required_left_untouched_when_adapter_already_set_it(self) -> None:
        endpoint = _endpoint(auth_required=True)
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.auth_required is True

    def test_auth_required_not_set_by_non_protects_edge(self) -> None:
        """An AUTH node that CALLS (not PROTECTS) the endpoint doesn't count as protection."""
        auth_node = _auth()
        endpoint = _endpoint()
        edge = Edge(
            source=auth_node.id,
            target=endpoint.id,
            relationship_type=RelationshipType.CALLS,
        )
        doc = AiSbomDocument(target="t", nodes=[auth_node, endpoint], edges=[edge])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.auth_required is False
