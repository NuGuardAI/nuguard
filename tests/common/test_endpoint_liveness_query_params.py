"""Liveness probe sends the SBOM's required query parameters (issue #653)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from nuguard.common.endpoint_liveness import _required_query_params, check_endpoint_liveness
from nuguard.sbom.models import (
    AiSbomDocument,
    HttpParameterLocation,
    HttpParameterMetadata,
    HttpRequestMetadata,
    Node,
    NodeMetadata,
)
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


class _ParamClient:
    """Answers ``without_params`` unless the probe sent query parameters."""

    def __init__(self, with_params: int = 200, without_params: int = 400) -> None:
        self.with_params = with_params
        self.without_params = without_params
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        self.calls.append((method.upper(), path, params))
        return (self.with_params if params else self.without_params), "reply", {}


def _param(
    name: str,
    location: HttpParameterLocation = "query",
    required: bool = True,
    type_hint: str = "string",
) -> HttpParameterMetadata:
    return HttpParameterMetadata(
        name=name, location=location, required=required, type_hint=type_hint
    )


def _node(path: str, *params: HttpParameterMetadata, **meta_kwargs: Any) -> Node:
    request = HttpRequestMetadata(methods=["GET"], parameters=list(params)) if params else None
    return Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(endpoint=path, method="GET", http_request=request, **meta_kwargs),
    )


@pytest.mark.asyncio
async def test_required_query_params_sent_so_get_endpoint_is_operational() -> None:
    node = _node("/customerSupportAgent", _param("sessionId"), _param("userMessage"))
    client = _ParamClient()

    report = await check_endpoint_liveness(AiSbomDocument(target="./app", nodes=[node]), client)

    assert client.calls == [
        (
            "GET",
            "/customerSupportAgent",
            {"sessionId": "nuguard-probe", "userMessage": "nuguard-probe"},
        )
    ]
    assert node.metadata.operational is True
    assert report.operational == 1
    assert report.non_operational == 0


@pytest.mark.asyncio
async def test_optional_and_non_query_params_are_not_sent() -> None:
    node = _node(
        "/search",
        _param("q", required=False),
        _param("X-Tenant", location="header"),
        _param("body", location="json"),
    )
    client = _ParamClient()

    await check_endpoint_liveness(AiSbomDocument(target="./app", nodes=[node]), client)

    assert client.calls == [("GET", "/search", None)]
    # Nothing was sent for the 400, so it still reads as a dead route.
    assert node.metadata.operational is False


@pytest.mark.asyncio
async def test_bare_400_without_declared_params_is_still_non_operational() -> None:
    node = _node("/api/gone")
    client = _ParamClient()

    await check_endpoint_liveness(AiSbomDocument(target="./app", nodes=[node]), client)

    assert client.calls == [("GET", "/api/gone", None)]
    assert node.metadata.operational is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 422])
async def test_validation_rejection_of_placeholders_means_route_exists(status: int) -> None:
    node = _node("/items", _param("limit", type_hint="widget"))
    client = _ParamClient(with_params=status)

    await check_endpoint_liveness(AiSbomDocument(target="./app", nodes=[node]), client)

    assert node.metadata.operational is True
    assert node.metadata.liveness_notes
    assert "placeholder" in node.metadata.liveness_notes[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 405, 500])
async def test_dead_statuses_still_non_operational_with_params(status: int) -> None:
    node = _node("/gone", _param("id"))
    client = _ParamClient(with_params=status)

    await check_endpoint_liveness(AiSbomDocument(target="./app", nodes=[node]), client)

    assert node.metadata.operational is False


def test_placeholder_values_follow_the_type_hint() -> None:
    node = _node(
        "/typed",
        _param("count", type_hint="Integer"),
        _param("ratio", type_hint="double"),
        _param("flag", type_hint="boolean"),
        _param("name"),
    )

    assert _required_query_params(node.metadata) == {
        "count": "1",
        "ratio": "1.0",
        "flag": "true",
        "name": "nuguard-probe",
    }


def test_no_http_request_means_no_params() -> None:
    assert _required_query_params(_node("/plain").metadata) == {}
