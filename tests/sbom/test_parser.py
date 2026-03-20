"""Test parse_sbom with various input forms."""

from __future__ import annotations

import json

import pytest

from nuguard.common.errors import SbomError
from nuguard.models.sbom import AiSbomDocument, NodeType
from nuguard.sbom.parser import parse_sbom


def test_parse_from_dict(minimal_sbom_dict: dict) -> None:
    """parse_sbom accepts a pre-parsed dict."""
    doc = parse_sbom(minimal_sbom_dict)
    assert isinstance(doc, AiSbomDocument)
    assert doc.target == minimal_sbom_dict["target"]


def test_parse_from_json_string(minimal_sbom_dict: dict) -> None:
    """parse_sbom accepts a JSON string."""
    json_str = json.dumps(minimal_sbom_dict)
    doc = parse_sbom(json_str)
    assert isinstance(doc, AiSbomDocument)
    assert len(doc.nodes) == len(minimal_sbom_dict["nodes"])


def test_parse_from_bytes(minimal_sbom_dict: dict) -> None:
    """parse_sbom accepts UTF-8 bytes."""
    raw = json.dumps(minimal_sbom_dict).encode("utf-8")
    doc = parse_sbom(raw)
    assert isinstance(doc, AiSbomDocument)


def test_parse_invalid_json_raises() -> None:
    """parse_sbom raises SbomError for malformed JSON."""
    with pytest.raises(SbomError, match="Failed to decode SBOM JSON"):
        parse_sbom("{not valid json")


def test_parse_wrong_type_raises() -> None:
    """parse_sbom raises SbomError for unsupported input types."""
    with pytest.raises(SbomError):
        parse_sbom(12345)  # type: ignore[arg-type]


def test_parse_nodes_typed_correctly(minimal_sbom_dict: dict) -> None:
    """Parsed nodes have the correct NodeType enum values."""
    doc = parse_sbom(minimal_sbom_dict)
    node_types = {n.component_type for n in doc.nodes}
    assert NodeType.AGENT in node_types
    assert NodeType.DATASTORE in node_types


def test_parse_missing_required_field_raises() -> None:
    """parse_sbom raises SbomError when a required field is missing."""
    incomplete = {
        "schema_version": "1.3.0",
        # missing: generated_at, target
        "nodes": [],
        "edges": [],
    }
    with pytest.raises(SbomError, match="Failed to parse SBOM document"):
        parse_sbom(incomplete)
