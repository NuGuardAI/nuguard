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

    def test_enrich_is_idempotent_on_second_call(self) -> None:
        """Re-running enrich() on an already-enriched doc must not change anything.

        This matters because analyze/behavior/redteam now call enrich() again
        on load to self-heal stale SBOM files, even when the file is already
        fresh.
        """
        auth_node = _auth()
        endpoint = _endpoint(endpoint="/users/{user_id}")
        edge = Edge(
            source=auth_node.id,
            target=endpoint.id,
            relationship_type=RelationshipType.PROTECTS,
        )
        doc = AiSbomDocument(target="t", nodes=[auth_node, endpoint], edges=[edge])

        enrich(doc)
        first_pass = doc.model_dump_json()

        enrich(doc)
        second_pass = doc.model_dump_json()

        assert first_pass == second_pass

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


class TestApiEndpointPathParams:
    """Path-param extraction feeds idor_surface and the two-step chat bootstrap
    flow (tests/apps/studyield-app/2-step-chat.md) — must cover both
    FastAPI/ASP.NET Core's {id} style and NestJS/Express's :id style."""

    def test_brace_style_param_extracted(self) -> None:
        endpoint = _endpoint(endpoint="/users/{user_id}")
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.path_params == ["user_id"]

    def test_colon_style_param_extracted(self) -> None:
        endpoint = _endpoint(endpoint="/chat/conversations/:id/messages")
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.path_params == ["id"]

    def test_colon_style_multiple_params_extracted_in_order(self) -> None:
        endpoint = _endpoint(endpoint="/orgs/:orgId/projects/:projectId/chat")
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.path_params == ["orgId", "projectId"]

    def test_no_params_leaves_path_params_unset(self) -> None:
        endpoint = _endpoint(endpoint="/chat")
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert not enriched.metadata.path_params

    def test_colon_style_sets_idor_surface_for_scoped_param(self) -> None:
        endpoint = _endpoint(endpoint="/accounts/:account_id/transfer")
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.idor_surface is True

    def test_adapter_provided_path_params_left_untouched(self) -> None:
        """An adapter that already populated path_params (e.g. from its own
        AST/regex route parsing) takes precedence over the generic regex."""
        endpoint = _endpoint(endpoint="/chat/conversations/:id/messages", path_params=["id"])
        doc = AiSbomDocument(target="t", nodes=[endpoint], edges=[])

        enrich(doc)

        enriched = next(n for n in doc.nodes if n.id == endpoint.id)
        assert enriched.metadata.path_params == ["id"]
