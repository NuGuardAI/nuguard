"""Redteam endpoint precedence regressions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from nuguard.common.endpoint_detection import EndpointSource, PayloadShape, ResolvedEndpoint
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


def _endpoint_node(path: str, *, key: str = "message", payload_list: bool = False) -> Node:
    node_id = uuid.uuid5(_NS, f"API_ENDPOINT/{path}")
    return Node(
        id=node_id,
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.99,
        metadata=NodeMetadata(
            endpoint=path,
            method="POST",
            chat_payload_key=key,
            chat_payload_list=payload_list,
        ),
    )


def test_redteam_explicit_endpoint_wins_over_sbom_candidate() -> None:
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_endpoint_node("/api/chat/message")],
        edges=[],
    )

    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:8080",
        chat_path="/api/agent/chat",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    assert orchestrator.resolved_chat_path == "/api/agent/chat"
    assert orchestrator.resolved_chat_path_source == "config"


def test_redteam_uses_sbom_endpoint_when_not_configured() -> None:
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_endpoint_node("/api/chat/message")],
        edges=[],
    )

    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:8080",
        chat_path="",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    assert orchestrator.resolved_chat_path == "/api/chat/message"
    assert orchestrator.resolved_chat_path_source == "sbom"


@pytest.mark.asyncio
async def test_redteam_live_resolution_uses_common_resolver(monkeypatch) -> None:
    sbom = AiSbomDocument(target="./app", nodes=[], edges=[])
    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:8080",
        chat_path="",
        chat_payload_key="message",
        chat_payload_list=False,
    )
    resolver = AsyncMock(
        return_value=ResolvedEndpoint(
            path="/api/chat",
            payload=PayloadShape(
                key="prompt",
                is_list=True,
                value_template={"content": ""},
                source=EndpointSource.PROBE,
            ),
            path_source=EndpointSource.PROBE,
        )
    )
    monkeypatch.setattr(
        "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
        resolver,
    )

    await orchestrator._maybe_probe_endpoints()

    resolver.assert_awaited_once()
    assert orchestrator.resolved_chat_path == "/api/chat"
    assert orchestrator._chat_payload_key == "prompt"
    assert orchestrator._chat_payload_list is True
    assert orchestrator._chat_payload_value_template == {"content": ""}
    assert orchestrator.resolved_chat_path_source == "probe"


@pytest.mark.asyncio
async def test_redteam_live_resolution_reports_sbom_source(monkeypatch) -> None:
    """When the common resolver finds the endpoint via its own SBOM step (not a
    live probe), resolved_chat_path_source must report 'sbom', not stay stuck
    on the stale 'default' placeholder set in __init__."""
    sbom = AiSbomDocument(target="./app", nodes=[], edges=[])
    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:8080",
        chat_path="",
        chat_payload_key="message",
        chat_payload_list=False,
    )
    resolver = AsyncMock(
        return_value=ResolvedEndpoint(
            path="/api/chat/message",
            payload=PayloadShape(key="message", is_list=False, source=EndpointSource.SBOM),
            path_source=EndpointSource.SBOM,
        )
    )
    monkeypatch.setattr(
        "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
        resolver,
    )

    await orchestrator._maybe_probe_endpoints()

    resolver.assert_awaited_once()
    assert orchestrator.resolved_chat_path == "/api/chat/message"
    assert orchestrator.resolved_chat_path_source == "sbom"


@pytest.mark.asyncio
async def test_redteam_maybe_probe_uses_cached_confirmed_endpoint(monkeypatch) -> None:
    """Regression: when the enriched SBOM already has a runtime-probe-confirmed
    endpoint, _maybe_probe_endpoints() must use it directly and skip calling
    resolve_chat_endpoint() (and therefore the live-probe sweep) entirely —
    even when the confirmed path (e.g. "/extract") doesn't rank as a
    chat-like keyword candidate."""
    confirmed_node = Node(
        id=uuid.uuid5(_NS, "API_ENDPOINT/ANY//extract"),
        name="ANY /extract",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.8,
        metadata=NodeMetadata(
            endpoint="/extract",
            method="ANY",
            chat_payload_key="text",
            chat_payload_list=False,
            extras={
                "source": "runtime_probe",
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            },
        ),
    )
    sbom = AiSbomDocument(target="./app", nodes=[confirmed_node], edges=[])
    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:8080",
        chat_path="",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    resolver = AsyncMock()
    monkeypatch.setattr(
        "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
        resolver,
    )

    await orchestrator._maybe_probe_endpoints()

    resolver.assert_not_awaited()
    assert orchestrator.resolved_chat_path == "/extract"
    assert orchestrator._chat_payload_key == "text"
