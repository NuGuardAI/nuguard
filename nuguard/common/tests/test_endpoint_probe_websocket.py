"""Tests for WebSocket endpoint detection in nuguard.common.endpoint_probe.

Covers the live upgrade-probe path (Layer 1, item 2 of the WebSocket support
plan): an SBOM-declared or fallback WS candidate path answering an HTTP
Upgrade request with 101/426 must be returned as a ProbeResult with the
``__websocket__`` payload-key marker.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from nuguard.common.endpoint_probe import probe_chat_endpoints
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

BASE = "http://test-app"
_NS = uuid.NAMESPACE_URL


def _ws_node(path: str) -> Node:
    return Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/WEBSOCKET/{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(endpoint=path, method="WEBSOCKET"),
    )


def _empty_sbom() -> AiSbomDocument:
    return AiSbomDocument(target="./app", nodes=[])


@pytest.mark.asyncio
@respx.mock
async def test_probe_detects_sbom_declared_websocket_endpoint():
    sbom = AiSbomDocument(target="./app", nodes=[_ws_node("/ws/chat")])
    respx.get(f"{BASE}/ws/chat").mock(return_value=httpx.Response(101))

    result = await probe_chat_endpoints(BASE, sbom)

    assert result is not None
    assert result.path == "/ws/chat"
    assert result.key == "__websocket__"


@pytest.mark.asyncio
@respx.mock
async def test_probe_detects_fallback_websocket_path_when_sbom_empty():
    respx.get(f"{BASE}/ws").mock(
        return_value=httpx.Response(426, headers={"Upgrade": "websocket"})
    )

    result = await probe_chat_endpoints(BASE, _empty_sbom())

    assert result is not None
    assert result.path == "/ws"
    assert result.key == "__websocket__"


@pytest.mark.asyncio
@respx.mock
async def test_probe_ignores_bare_426_with_no_upgrade_header():
    """Regression: a plain HTTP POST-only endpoint that happens to answer a GET
    with 426 (e.g. Cloud Run / framework generic "wrong method" error, not an
    actual WS rejection) must NOT be misdetected as a WebSocket endpoint — this
    broke a real deployed app's chat endpoint (gemini-auto, /api/agent/chat).
    """
    sbom = AiSbomDocument(target="./app", nodes=[_ws_node("/api/agent/chat")])
    respx.get(f"{BASE}/api/agent/chat").mock(return_value=httpx.Response(426))
    # Blind POST probe also rejects every shape so the only way this test could
    # pass is via the (buggy) WS upgrade probe accepting the bare 426.
    respx.post(f"{BASE}/api/agent/chat").mock(return_value=httpx.Response(422))

    result = await probe_chat_endpoints(BASE, sbom, hint_path="/api/agent/chat")

    assert result is None or result.key != "__websocket__"
