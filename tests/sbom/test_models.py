"""Test AiSbomDocument round-trip JSON serialization."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from nuguard.models.sbom import (
    AiSbomDocument,
    DataClassification,
    DatastoreType,
    Edge,
    EdgeRelationshipType,
    Node,
    NodeMetadata,
    NodeType,
    ScanSummary,
)
from nuguard.sbom.extractor.serializer import AiSbomSerializer


def test_roundtrip_json(minimal_sbom_doc: AiSbomDocument) -> None:
    """Serializing then deserializing an AiSbomDocument is lossless."""
    json_str = AiSbomSerializer.to_json(minimal_sbom_doc)
    recovered = AiSbomSerializer.from_json(json_str)

    assert recovered.schema_version == minimal_sbom_doc.schema_version
    assert recovered.target == minimal_sbom_doc.target
    assert recovered.generator == minimal_sbom_doc.generator
    assert len(recovered.nodes) == len(minimal_sbom_doc.nodes)
    assert len(recovered.edges) == len(minimal_sbom_doc.edges)


def test_roundtrip_from_dict(minimal_sbom_dict: dict) -> None:
    """from_json accepts a pre-parsed dict."""
    doc = AiSbomSerializer.from_json(minimal_sbom_dict)
    assert doc.target == minimal_sbom_dict["target"]
    assert len(doc.nodes) == len(minimal_sbom_dict["nodes"])


def test_node_type_preserved(minimal_sbom_doc: AiSbomDocument) -> None:
    """Node component_type survives a JSON round-trip as an enum."""
    json_str = AiSbomSerializer.to_json(minimal_sbom_doc)
    recovered = AiSbomSerializer.from_json(json_str)
    node_types = {n.component_type for n in recovered.nodes}
    assert NodeType.AGENT in node_types
    assert NodeType.TOOL in node_types
    assert NodeType.DATASTORE in node_types


def test_edge_access_type_preserved(minimal_sbom_doc: AiSbomDocument) -> None:
    """access_type on ACCESSES edges is preserved after round-trip."""
    json_str = AiSbomSerializer.to_json(minimal_sbom_doc)
    recovered = AiSbomSerializer.from_json(json_str)
    accesses_edges = [
        e for e in recovered.edges if e.relationship_type == EdgeRelationshipType.ACCESSES
    ]
    assert len(accesses_edges) == 1
    assert accesses_edges[0].access_type is not None


def test_summary_node_counts(minimal_sbom_doc: AiSbomDocument) -> None:
    """ScanSummary node_counts round-trips correctly."""
    json_str = AiSbomSerializer.to_json(minimal_sbom_doc)
    recovered = AiSbomSerializer.from_json(json_str)
    assert recovered.summary.node_counts == {"AGENT": 1, "TOOL": 1, "DATASTORE": 1}


def test_cyclonedx_export(minimal_sbom_doc: AiSbomDocument) -> None:
    """to_cyclonedx returns a CycloneDX 1.6 compliant dict."""
    from nuguard.models.sbom import PackageDep

    minimal_sbom_doc.deps = [
        PackageDep(name="langchain", version_spec=">=0.2", ecosystem="pypi"),
        PackageDep(name="openai", version_spec=">=1.0", ecosystem="pypi"),
    ]
    cdx = AiSbomSerializer.to_cyclonedx(minimal_sbom_doc)

    assert cdx["bomFormat"] == "CycloneDX"
    assert cdx["specVersion"] == "1.6"
    assert len(cdx["components"]) == 2
    names = {c["name"] for c in cdx["components"]}
    assert "langchain" in names
    assert "openai" in names


def test_empty_document_roundtrip() -> None:
    """A minimal document with no nodes/edges round-trips cleanly."""
    doc = AiSbomDocument(
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        target="empty-app",
    )
    json_str = AiSbomSerializer.to_json(doc)
    recovered = AiSbomSerializer.from_json(json_str)
    assert recovered.nodes == []
    assert recovered.edges == []
    assert recovered.target == "empty-app"
