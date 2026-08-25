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
