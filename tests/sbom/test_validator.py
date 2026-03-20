"""Test SBOM schema validation."""

from __future__ import annotations

import pytest

from nuguard.common.errors import ValidationError
from nuguard.sbom.validator import validate_sbom


def test_valid_doc_passes(minimal_sbom_dict: dict) -> None:
    """A well-formed SBOM dict passes validation without raising."""
    validate_sbom(minimal_sbom_dict)  # should not raise


def test_missing_required_field_fails() -> None:
    """A document missing 'schema_version' raises ValidationError."""
    bad = {
        # "schema_version": "1.3.0",  # intentionally omitted
        "generated_at": "2026-03-20T12:00:00+00:00",
        "target": "test-app",
        "nodes": [],
        "edges": [],
    }
    with pytest.raises(ValidationError):
        validate_sbom(bad)


def test_invalid_component_type_fails(minimal_sbom_dict: dict) -> None:
    """An unknown component_type value raises ValidationError."""
    bad = dict(minimal_sbom_dict)
    bad["nodes"] = [
        {
            "id": "x1",
            "name": "UnknownNode",
            "component_type": "UNKNOWN_TYPE",  # not in the enum
        }
    ]
    with pytest.raises(ValidationError):
        validate_sbom(bad)


def test_invalid_relationship_type_fails(minimal_sbom_dict: dict) -> None:
    """An unknown relationship_type value raises ValidationError."""
    bad = dict(minimal_sbom_dict)
    bad["edges"] = [
        {
            "source": "agent001",
            "target": "tool001",
            "relationship_type": "DOES_SOMETHING_WEIRD",  # not in the enum
        }
    ]
    with pytest.raises(ValidationError):
        validate_sbom(bad)


def test_missing_node_id_fails(minimal_sbom_dict: dict) -> None:
    """A node missing the required 'id' field raises ValidationError."""
    bad = dict(minimal_sbom_dict)
    bad["nodes"] = [
        {
            # "id": "agent001",  # intentionally omitted
            "name": "CustomerSupportAgent",
            "component_type": "AGENT",
        }
    ]
    with pytest.raises(ValidationError):
        validate_sbom(bad)


def test_minimal_valid_doc_passes() -> None:
    """A minimal doc with all required top-level fields passes."""
    data = {
        "schema_version": "1.3.0",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "target": "my-app",
        "nodes": [],
        "edges": [],
    }
    validate_sbom(data)  # should not raise


def test_node_confidence_out_of_range_fails(minimal_sbom_dict: dict) -> None:
    """A node confidence value > 1.0 fails schema validation."""
    bad = dict(minimal_sbom_dict)
    bad["nodes"] = [
        {
            "id": "n1",
            "name": "Agent",
            "component_type": "AGENT",
            "confidence": 1.5,  # > 1.0 — invalid
        }
    ]
    with pytest.raises(ValidationError):
        validate_sbom(bad)
