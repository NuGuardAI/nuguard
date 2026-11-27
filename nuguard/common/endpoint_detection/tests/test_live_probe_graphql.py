"""GraphQL endpoint detection (#760, first slice): ``{ __typename }`` ping."""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.common.endpoint_detection.live_probe import probe_chat_endpoints

TARGET = "http://target.test"


@pytest.mark.anyio
@respx.mock
async def test_graphql_endpoint_detected_via_typename_ping() -> None:
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    route = respx.post(f"{TARGET}/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"__typename": "Query"}})
    )
    # Every other path, including flat REST bodies on /graphql's siblings, 400s.
    respx.post(url__regex=r".*").mock(
        return_value=httpx.Response(400, json={"errors": [{"message": "Must provide query string"}]})
    )

    result = await probe_chat_endpoints(TARGET, sbom=None)

    assert result is not None
    assert (result.path, result.key) == ("/graphql", "__graphql__")
    assert b"__typename" in route.calls[0].request.content
