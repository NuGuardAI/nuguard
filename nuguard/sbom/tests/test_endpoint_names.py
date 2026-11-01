"""Endpoint labels stay concise and unique across repeated handler names."""

from __future__ import annotations

from nuguard.sbom.extractor.endpoint_names import disambiguate_endpoint_names
from nuguard.sbom.models import Node, NodeMetadata
from nuguard.sbom.types import ComponentType


def _endpoint(name: str, route: str) -> Node:
    return Node(
        name=name,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(method="DELETE", endpoint=route),
    )


def test_repeated_names_use_short_resource_labels() -> None:
    collection = _endpoint("Delete", "/api/v1/notes")
    member = _endpoint("Delete", "/api/v1/notes/:id")
    documents = _endpoint("Delete", "/api/v1/documents/:id")
    unique = _endpoint("Delete All", "/api/v1/notifications")

    disambiguate_endpoint_names([collection, member, documents, unique])

    assert collection.name == "Delete Notes"
    assert member.name == "Delete Notes By Id"
    assert documents.name == "Delete Documents"
    assert unique.name == "Delete All"


def test_identical_routes_use_short_unique_suffixes() -> None:
    first = _endpoint("Delete", "/api/v1/notes/:id")
    second = _endpoint("Delete", "/api/v1/notes/:id")
    third = _endpoint("Delete", "/api/v1/notes/:id")
    reserved = _endpoint("Delete #1", "/legacy")

    disambiguate_endpoint_names([first, second, third, reserved])

    assert {first.name, second.name, third.name, reserved.name} == {
        "Delete Notes",
        "Delete Notes By Id",
        "Delete #1",
        "Delete #2",
    }
    disambiguate_endpoint_names([first, second, third, reserved])
    assert {first.name, second.name, third.name, reserved.name} == {
        "Delete Notes",
        "Delete Notes By Id",
        "Delete #1",
        "Delete #2",
    }
