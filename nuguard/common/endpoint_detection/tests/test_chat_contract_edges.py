"""Adversarial discovery contracts and browser precedence for issue #627."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from nuguard.common.endpoint_detection.constants import TEST_MESSAGE
from nuguard.common.endpoint_detection.live_probe import (
    _message_field_required,
    _try_openapi_detection,
    probe_endpoint,
)
from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape
from nuguard.common.endpoint_detection.resolver import resolve_chat_endpoint

BASE = "https://contract.test"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 405, 500, 503])
@respx.mock
async def test_failed_control_is_not_request_field_evidence(status: int) -> None:
    route = respx.post(f"{BASE}/chat").mock(
        return_value=httpx.Response(status, json={"error": "text is required"})
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        assert not await _message_field_required(
            client, "/chat", {"text": "hello", "profile": "test"}, "text"
        )
    assert json.loads(route.calls[0].request.content) == {"profile": "test"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), httpx.ReadTimeout("timeout")])
@respx.mock
async def test_transport_failure_on_control_is_not_evidence(failure: Exception) -> None:
    respx.post(f"{BASE}/chat").mock(side_effect=failure)
    async with httpx.AsyncClient(base_url=BASE) as client:
        assert not await _message_field_required(client, "/chat", {"text": "hello"}, "text")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,expected",
    [
        (400, {"error": "text is required"}, True),
        (400, {"error": '"text" (string) is required'}, True),
        (400, {"error": '"profile" (string) is required; text was ignored'}, False),
        (400, {"error": '"text" (string is required'}, False),
        (
            422,
            {"detail": [{"loc": ["body", "text"], "msg": "Field required", "type": "missing"}]},
            True,
        ),
        (400, {"error": "Request body must not be empty"}, False),
        (422, {"detail": "Invalid request body"}, False),
        (400, {"error": "profile is required"}, False),
        (400, {"error": "profile is required; text was ignored"}, False),
        (200, {"error": "text is required"}, True),
        (200, {"error": "profile is required"}, False),
        (200, {"error": "text upstream timeout"}, False),
        (200, {"message": "Welcome", "session_id": "new-id"}, False),
    ],
)
@respx.mock
async def test_control_requires_validation_of_the_specific_field(
    status: int, body: dict, expected: bool
) -> None:
    respx.post(f"{BASE}/chat").mock(return_value=httpx.Response(status, json=body))
    async with httpx.AsyncClient(base_url=BASE) as client:
        assert await _message_field_required(client, "/chat", {"text": "hello"}, "text") is expected


@pytest.mark.asyncio
@respx.mock
async def test_nonempty_body_validation_cannot_confirm_an_ignored_field() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not body:
            return httpx.Response(400, json={"error": "Request body must not be empty"})
        return httpx.Response(200, json={"outputs": [{"text": "Welcome"}]})

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{BASE}/chat").mock(side_effect=reply)
    result = await probe_endpoint(BASE, None, hint_path="/chat")
    assert result is not None and not result.confirmed


def _schema(properties: dict) -> dict:
    return {
        "openapi": "3.0.0",
        "paths": {
            "/chat": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": properties}
                            }
                        },
                    }
                }
            }
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "property_schema,is_list",
    [
        ({"type": "string"}, False),
        (
            {
                "type": "object",
                "properties": {"content": {"type": "string"}, "role": {"type": "string"}},
            },
            False,
        ),
        (
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}, "role": {"type": "string"}},
                },
            },
            True,
        ),
    ],
)
@respx.mock
async def test_openapi_contract_sends_the_declared_value_shape(
    property_schema: dict, is_list: bool
) -> None:
    respx.get(f"{BASE}/openapi.json").mock(
        return_value=httpx.Response(200, json=_schema({"messages": property_schema}))
    )

    def reply(request: httpx.Request) -> httpx.Response:
        value = json.loads(request.content)["messages"]
        if property_schema["type"] == "object":
            assert isinstance(value, dict) and value["content"]
        elif is_list:
            assert isinstance(value, list) and isinstance(value[0], dict) and value[0]["content"]
        else:
            assert isinstance(value, str) and value
        return httpx.Response(200, json={"outputs": [{"text": "Processed request"}]})

    route = respx.post(f"{BASE}/chat").mock(side_effect=reply)
    async with httpx.AsyncClient(base_url=BASE) as client:
        result = await _try_openapi_detection(client, 1, None, None)
    assert result is not None and result.confirmed and result.is_list == is_list
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_openapi_template_belongs_to_the_selected_route() -> None:
    schema = _schema({"message": {"type": "object", "properties": {"content": {"type": "string"}}}})
    schema["paths"]["/chat/less-likely"] = {
        "post": {
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"properties": {"message": {"type": "string"}}}},
                }
            }
        }
    }
    respx.get(f"{BASE}/openapi.json").mock(return_value=httpx.Response(200, json=schema))

    def reply(request: httpx.Request) -> httpx.Response:
        assert isinstance(json.loads(request.content)["message"], dict)
        return httpx.Response(200, json={"response": "Processed request"})

    respx.post(f"{BASE}/chat").mock(side_effect=reply)
    async with httpx.AsyncClient(base_url=BASE) as client:
        result = await _try_openapi_detection(client, 1, None, None)
    assert result is not None and result.confirmed and result.path == "/chat"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_error", [None, "text is required"])
@respx.mock
async def test_llm_guessed_openapi_field_needs_validation_evidence(
    missing_error: str | None,
) -> None:
    respx.get(f"{BASE}/openapi.json").mock(
        return_value=httpx.Response(200, json={"openapi": "3.0.0", "paths": {}})
    )

    def reply(request: httpx.Request) -> httpx.Response:
        if "text" not in json.loads(request.content) and missing_error:
            return httpx.Response(422, json={"detail": missing_error})
        return httpx.Response(200, json={"outputs": [{"text": "Welcome"}]})

    route = respx.post(f"{BASE}/chat").mock(side_effect=reply)
    with patch(
        "nuguard.common.endpoint_detection.live_probe._llm_chat_key_from_openapi",
        new=AsyncMock(return_value=("/chat", "text", False)),
    ):
        async with httpx.AsyncClient(base_url=BASE) as client:
            result = await _try_openapi_detection(client, 1, None, None, llm=AsyncMock())
    assert (result is not None and result.confirmed) is bool(missing_error)
    assert route.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
@respx.mock
async def test_stale_or_inaccessible_openapi_contract_is_not_confirmed(status: int) -> None:
    respx.get(f"{BASE}/openapi.json").mock(
        return_value=httpx.Response(200, json=_schema({"message": {"type": "string"}}))
    )
    respx.post(f"{BASE}/chat").mock(return_value=httpx.Response(status))
    async with httpx.AsyncClient(base_url=BASE) as client:
        result = await _try_openapi_detection(client, 1, None, None)
    assert result is None or not result.confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "observed", [None, ("/other", PayloadShape(key="text", source=EndpointSource.BROWSER))]
)
@respx.mock
async def test_browser_cannot_replace_explicit_route(observed: tuple | None) -> None:
    respx.post(f"{BASE}/chat").mock(
        return_value=httpx.Response(200, json={"outputs": [{"text": "Welcome"}]})
    )
    with patch(
        "nuguard.common.endpoint_detection.resolver.detect_with_browser",
        new=AsyncMock(return_value=observed),
    ):
        result = await resolve_chat_endpoint(
            BASE, None, endpoint="/chat", enable_browser_fallback=True
        )
    assert result.path == "/chat"
    assert result.path_source is EndpointSource.CONFIG
    assert result.payload.source is EndpointSource.FALLBACK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type", ["text/event-stream", "application/x-ndjson", "application/jsonl"]
)
@pytest.mark.parametrize(
    "events,response_key,expected",
    [
        ([], None, False),
        ([{"type": "status", "content": "Preparing response"}], None, False),
        ([{"session_id": "new-id"}], None, False),
        ([{"error": "Backend unavailable"}], None, False),
        ([{"detail": "Invalid request", "message": "text is required"}], None, False),
        ([{"outputs": [{"text": "Actual reply"}]}], None, True),
        (
            [
                {"choices": [{"delta": {"content": "Actual "}}]},
                {"choices": [{"delta": {"content": "reply"}}]},
            ],
            None,
            True,
        ),
        (
            [{"type": "status", "content": "Preparing"}, {"custom": {"reply": "Actual reply"}}],
            "custom.reply",
            True,
        ),
    ],
)
@respx.mock
async def test_streaming_runtime_and_preflight_share_reply_contract(
    content_type: str,
    events: list[dict],
    response_key: str | None,
    expected: bool,
) -> None:
    from nuguard.common.endpoint_preflight import validate_and_rotate_chat_endpoint
    from nuguard.redteam.target.client import TargetAppClient

    if content_type == "text/event-stream":
        stream = (
            "data: invalid-json\n\n"
            + "".join(f"data: {json.dumps(event)}\n\n" for event in events)
            + "data: [DONE]\n\n"
        )
    else:
        stream = "invalid-json\n" + "\n".join(json.dumps(event) for event in events)
    respx.post(f"{BASE}/chat").mock(
        return_value=httpx.Response(200, headers={"content-type": content_type}, text=stream)
    )
    client = TargetAppClient(
        BASE, chat_path="/chat", chat_payload_key="text", chat_response_key=response_key
    )
    outcome = await validate_and_rotate_chat_endpoint(
        client, None, target_url=BASE, has_explicit_endpoint=True
    )
    assert outcome.ok is expected and outcome.cacheable is expected
    if expected:
        assert client.last_raw_response == "Actual reply"


@pytest.mark.asyncio
@respx.mock
async def test_shared_omission_response_is_evaluated_for_each_candidate_key() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not body:
            return httpx.Response(422, json={"detail": "text is required"})
        return httpx.Response(
            200, json={"outputs": [{"text": "Processed" if "text" in body else "Welcome"}]}
        )

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    route = respx.post(f"{BASE}/chat").mock(side_effect=reply)
    result = await probe_endpoint(BASE, None, hint_path="/chat")
    assert result is not None and result.confirmed and result.key == "text"
    assert sum(json.loads(call.request.content) == {} for call in route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_gemini_typed_validation_confirms_configured_route() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        if "message" not in json.loads(request.content):
            return httpx.Response(400, json={"error": '"message" (string) is required'})
        return httpx.Response(200, json={"text": "Hello", "sources": [], "vehicleUpdates": {}})

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    route = respx.post(f"{BASE}/api/agent/chat").mock(side_effect=reply)
    result = await resolve_chat_endpoint(BASE, None, endpoint="/api/agent/chat")
    assert result.path == "/api/agent/chat"
    assert result.path_source is EndpointSource.CONFIG
    assert result.payload.key == "message"
    assert result.payload.source is EndpointSource.PROBE
    assert [json.loads(call.request.content) for call in route.calls] == [
        {"message": TEST_MESSAGE}, {},
    ]
