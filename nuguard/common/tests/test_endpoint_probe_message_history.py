"""Test that live chat-endpoint probing tries an OpenAI-style messages=[...] shape.

Companion to nuguard/redteam/tests/test_message_history_payload.py: that file
covers TargetAppClient shaping the body once a 'messages' key is known; this
covers probe_chat_endpoints() discovering such an endpoint in the first place
when no SBOM candidate provided one (e.g. no chat_payload_key was detected).
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from nuguard.common.endpoint_probe import probe_chat_endpoints
from nuguard.sbom.models import AiSbomDocument

BASE = "http://test-app"


def _empty_sbom() -> AiSbomDocument:
    return AiSbomDocument(target="./app", nodes=[])


@pytest.mark.asyncio
@respx.mock
async def test_probe_discovers_messages_shaped_endpoint():
    """An endpoint that 422s on every flat-string shape but accepts
    messages=[{role,content}] must still be discovered."""

    def _responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if isinstance(body.get("messages"), list) and body["messages"] and isinstance(
            body["messages"][0], dict
        ) and "role" in body["messages"][0]:
            return httpx.Response(200, json={"response": "hello back"})
        return httpx.Response(422, json={"detail": "invalid shape"})

    respx.post(f"{BASE}/api/chat").mock(side_effect=_responder)
    result = await probe_chat_endpoints(BASE, _empty_sbom())
    assert result is not None
    path, payload_key, payload_list = result
    assert path == "/api/chat"
    assert payload_key == "messages"
    assert payload_list is True


@pytest.mark.asyncio
@respx.mock
async def test_probe_rejects_sse_error_envelope_and_finds_messages_shape():
    """Regression: a Vercel-AI-SDK-style SSE chat endpoint (e.g. OWASP Juice
    Shop's /rest/chat) always returns HTTP 200 with Content-Type:
    text/event-stream — even when the request body has the wrong shape and
    the app itself streams back an ``{"error": "..."}`` event because its own
    ``messages`` field was empty. The blind prober used to accept the very
    first shape it tried purely because the transport looked like a stream,
    silently locking in a payload key that never produces a real response.
    It must keep trying shapes until the SSE stream's first parsed event is
    not an error-only envelope.
    """

    def _sse_body(text: str | None) -> bytes:
        if text is None:
            return b'data: {"error": "LLM error: messages must not be empty"}\n\ndata: [DONE]\n\n'
        return f'data: {{"choices": [{{"delta": {{"content": {json.dumps(text)}}}}}]}}\n\ndata: [DONE]\n\n'.encode()

    def _responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages = body.get("messages")
        ok = (
            isinstance(messages, list)
            and messages
            and isinstance(messages[0], dict)
            and "role" in messages[0]
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_body("hello back" if ok else None),
        )

    respx.post(f"{BASE}/api/chat").mock(side_effect=_responder)
    result = await probe_chat_endpoints(BASE, _empty_sbom())
    assert result is not None
    path, payload_key, payload_list = result
    assert path == "/api/chat"
    assert payload_key == "messages"
    assert payload_list is True
