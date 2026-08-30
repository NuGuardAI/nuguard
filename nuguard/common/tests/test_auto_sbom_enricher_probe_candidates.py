"""Tests for auto_sbom_enricher's live-probe candidate path selection.

Regression for a crash observed against OWASP Juice Shop: the bounded
liveness probe run before behavior/redteam scenario generation sent a raw
GET to the literal SBOM endpoint path, including unresolved path-parameter
placeholders (e.g. ``/api/Recycles/:id``). Juice Shop's Express route parses
``req.params.id`` as JSON and crashed with
``SyntaxError: Unexpected token ':', ":id" is not valid JSON`` when the
literal placeholder string was sent as the path segment.
"""
from __future__ import annotations

from nuguard.common.auto_sbom_enricher import _collect_probe_candidates
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType


def _endpoint_node(path: str) -> Node:
    return Node(
        name=f"{path} API",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.5,
        metadata=NodeMetadata(endpoint=path),
    )


def test_path_param_placeholders_excluded_from_probe_candidates():
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            _endpoint_node("/api/Recycles/:id"),
            _endpoint_node("/api/Products/{id}"),
            _endpoint_node("/api/Cards/<id>"),
            _endpoint_node("/api/Products"),
        ],
    )
    candidates = _collect_probe_candidates(sbom)
    assert "/api/Recycles/:id" not in candidates
    assert "/api/Products/{id}" not in candidates
    assert "/api/Cards/<id>" not in candidates
    assert "/api/Products" in candidates


def test_summary_api_endpoints_with_path_params_excluded():
    sbom = AiSbomDocument(
        target="./app",
        nodes=[],
        summary=ScanSummary(api_endpoints=["/api/Recycles/:id", "/api/Products"]),
    )
    candidates = _collect_probe_candidates(sbom)
    assert "/api/Recycles/:id" not in candidates
    assert "/api/Products" in candidates
