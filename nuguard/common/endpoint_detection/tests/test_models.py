"""Tests for endpoint detection result types."""

from nuguard.common.endpoint_detection import (
    UNSET,
    EndpointSource,
    PayloadShape,
    ResolvedEndpoint,
)


def test_unset_is_distinct_from_valid_default_values() -> None:
    assert UNSET != "message"
    assert UNSET is not False


def test_resolved_endpoint_exposes_payload_compatibility_properties() -> None:
    result = ResolvedEndpoint(
        path="/api/chat",
        payload=PayloadShape(
            key="message",
            is_list=False,
            source=EndpointSource.CONFIG,
        ),
        path_source=EndpointSource.CONFIG,
        path_explicit=True,
    )

    assert result.payload_key == "message"
    assert result.payload_list is False
    assert result.path == "/api/chat"
