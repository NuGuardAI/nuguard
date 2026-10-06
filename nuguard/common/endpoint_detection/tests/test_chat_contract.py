"""Generic HTTP chat contract regressions for issue #627."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from nuguard.common.endpoint_detection import UNSET
from nuguard.common.endpoint_detection.live_probe import (
    _looks_like_chat_response,
    probe_endpoint,
)
from nuguard.common.endpoint_detection.resolver import resolve_chat_endpoint

BASE = "https://chat.test"


@pytest.mark.parametrize("body,key", [
    ({"outputs": [{"text": "Actual reply"}]}, None),
    ({"outputs": [{"text": "Actual reply"}]}, "outputs[0].text"),
    ({"outputs": [{"text": "Actual reply"}]}, "outputs.0.text"),
    ({"answer": "Actual reply", "suggestions": ["Next question"]}, None),
    ({"messages": [{"role": "assistant", "content": "Actual reply"}]}, None),
    ({"message": {"content": "Actual reply"}}, None),
    ({"choices": [{"message": {"content": "Actual reply"}}]}, None),
    ({"custom": {"reply": "Actual reply"}}, "custom.reply"),
])
def test_recognizes_actual_reply_text(body: object, key: str | None) -> None:
    assert _looks_like_chat_response(body, key)


@pytest.mark.parametrize("body", [
    {}, {"outputs": []}, {"outputs": [{"text": "  "}]},
    {"outputs": [None, {"text": 42}]}, {"response": None},
    {"response": ""}, {"response": {"unexpected": "object"}},
    {"session_id": "id", "status": "ok"},
    {"error": "failed", "code": 500, "status": "error"},
    {"error": "failed", "message": {"content": "Not a reply"}},
])
def test_rejects_empty_malformed_metadata_and_error_bodies(body: object) -> None:
    assert not _looks_like_chat_response(body)


@pytest.mark.parametrize("body,path", [
    ({"error": "failed", "message": {"content": "Not a reply"}}, "message.content"),
    ({"type": "status", "custom": {"reply": "Preparing response"}}, "custom.reply"),
])
def test_configured_path_does_not_promote_errors_or_control_frames(body: dict, path: str) -> None:
    assert not _looks_like_chat_response(body, path)


@pytest.mark.asyncio
@pytest.mark.parametrize("key,is_list", [("text", False), ("prompt", False), ("messages", True)])
@respx.mock
async def test_blind_discovery_checks_that_message_field_is_required(key: str, is_list: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if key not in body:
            return httpx.Response(422, json={"detail": f"{key} is required"})
        return httpx.Response(200, json={"outputs": [{"text": "Actual reply"}]})

    route = respx.post(f"{BASE}/api/chat").mock(side_effect=respond)
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    result = await probe_endpoint(BASE, None, hint_path="/api/chat")
    assert result is not None and result.confirmed
    assert (result.key, result.is_list) == (key, is_list)
    assert any(key not in json.loads(call.request.content) for call in route.calls)


@pytest.mark.asyncio
@respx.mock
async def test_default_greeting_cannot_confirm_a_guessed_message_field() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        text = "Processed message" if body.get("text") else "Welcome to our service"
        return httpx.Response(200, json={"outputs": [{"text": text}], "session_id": "changing-id"})

    route = respx.post(f"{BASE}/api/chat").mock(side_effect=respond)
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    result = await probe_endpoint(BASE, None, hint_path="/api/chat")
    assert result is not None and not result.confirmed
    assert sum(json.loads(call.request.content) == {} for call in route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_explicit_custom_contract_needs_no_field_omission_request() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, json={"custom": {"reply": "Actual reply"}}),
    )
    result = await probe_endpoint(
        BASE, None, hint_path="/api/chat", known_payload_key="userUtterance",
        known_response_key="custom.reply",
    )
    assert result is not None and result.confirmed and result.key == "userUtterance"
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_ambiguous_reply_reaches_browser_field_discovery() -> None:
    from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, json={"outputs": [{"text": "Welcome"}]}),
    )
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))
    observed = ("/api/chat", PayloadShape(key="text", is_list=False, source=EndpointSource.BROWSER))
    with patch("nuguard.common.endpoint_detection.resolver.detect_with_browser", new=AsyncMock(return_value=observed)):
        result = await resolve_chat_endpoint(BASE, None, enable_browser_fallback=True)
    assert result.path == "/api/chat" and result.payload_key == "text"
    assert result.payload.source is EndpointSource.BROWSER


@pytest.mark.asyncio
@respx.mock
async def test_explicit_endpoint_unknown_field_does_not_use_default_guess() -> None:
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, json={"outputs": [{"text": "Welcome"}]}),
    )
    result = await resolve_chat_endpoint(BASE, None, endpoint="/api/chat", payload_key=UNSET)
    assert result.path == "/api/chat"
    assert result.path_source.value == "config"
    assert result.payload.source.value == "fallback"
    assert any("message field" in note for note in result.notes)


@pytest.mark.asyncio
@pytest.mark.parametrize("key,response,key_path", [
    ("text", {"outputs": [{"text": "Actual reply"}]}, "outputs[0].text"),
    ("userUtterance", {"bespoke": {"reply": "Actual reply"}}, "bespoke.reply"),
])
@respx.mock
async def test_sbom_request_contract_is_preserved(key: str, response: dict, key_path: str) -> None:
    from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, NodeType

    sbom = AiSbomDocument(target="./app", nodes=[Node(
        name="chat", component_type=NodeType.API_ENDPOINT, confidence=0.95,
        metadata=NodeMetadata(endpoint="/api/chat", method="POST", chat_payload_key=key, response_text_key=key_path),
    )])
    route = respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(200, json=response))
    result = await resolve_chat_endpoint(BASE, sbom)
    assert result.path == "/api/chat" and result.payload_key == key
    assert result.response_key == key_path
    assert route.call_count == 1
    assert key in json.loads(route.calls[0].request.content)


@pytest.mark.asyncio
@respx.mock
async def test_streaming_control_frames_do_not_hide_real_reply() -> None:
    stream = 'data: {"type":"status","content":"Preparing response"}\n\ndata: {"choices":[{"delta":{"content":"Actual reply"}}]}\n\ndata: [DONE]\n'
    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=stream,
    ))
    result = await probe_endpoint(BASE, None, hint_path="/api/chat", known_payload_key="messages", known_payload_list=True)
    assert result is not None and result.confirmed


@pytest.mark.asyncio
@respx.mock
async def test_custom_streaming_response_path_skips_progress_frames() -> None:
    stream = 'data: {"type":"status","content":"Preparing response"}\n\ndata: {"custom":{"reply":"Actual reply"}}\n\n'
    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=stream,
    ))
    result = await probe_endpoint(
        BASE, None, hint_path="/api/chat", known_payload_key="text", known_response_key="custom.reply",
    )
    assert result is not None and result.confirmed


@pytest.mark.asyncio
@respx.mock
async def test_streaming_error_report_fallback_cannot_confirm_preflight() -> None:
    from nuguard.common.endpoint_preflight import validate_and_rotate_chat_endpoint
    from nuguard.redteam.target.client import TargetAppClient

    route = respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text='data: {"error":"Backend unavailable"}\n\n',
    ))
    client = TargetAppClient(BASE, chat_path="/api/chat", chat_payload_key="text")
    outcome = await validate_and_rotate_chat_endpoint(client, None, target_url=BASE, has_explicit_endpoint=True)
    assert not outcome.ok and not outcome.cacheable
    route.mock(return_value=httpx.Response(200, json={"outputs": [{"text": "Actual reply"}]}))
    outcome = await validate_and_rotate_chat_endpoint(client, None, target_url=BASE, has_explicit_endpoint=True)
    assert outcome.ok and outcome.cacheable


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_on_field_control_aborts_discovery() -> None:
    from nuguard.common.errors import TargetRateLimitedError

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    route = respx.post(f"{BASE}/api/chat").mock(side_effect=[
        httpx.Response(200, json={"outputs": [{"text": "Actual reply"}]}),
        httpx.Response(429, headers={"Retry-After": "12"}),
    ])
    with pytest.raises(TargetRateLimitedError) as error:
        await probe_endpoint(BASE, None, hint_path="/api/chat")
    assert error.value.retry_after == 12 and route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_blind_control_preserves_extras_and_never_claims_backend_failure_is_field_evidence() -> None:
    requests: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "text" in body:
            return httpx.Response(200, json={"outputs": [{"text": "Actual reply"}]})
        if len(body) == 1:
            return httpx.Response(500, json={"error": "Backend unavailable"})
        return httpx.Response(400, json={"error": "text is required"})

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{BASE}/api/chat").mock(side_effect=respond)
    result = await probe_endpoint(BASE, None, hint_path="/api/chat", probe_payload_extras={"profile": "test"})
    assert result is not None and not result.confirmed
    assert all(body["profile"] == "test" for body in requests)
