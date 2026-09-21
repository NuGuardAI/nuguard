"""Redteam endpoint precedence regressions."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from nuguard.common.endpoint_detection import (
    UNSET,
    EndpointSource,
    PayloadShape,
    ResolvedEndpoint,
)
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
async def test_redteam_maybe_probe_passes_unset_when_endpoint_not_explicit(monkeypatch) -> None:
    """Regression: when the caller never configured chat_path, __init__'s own
    plain SBOM guess (e.g. the generic "/chat" fallback) must NOT be forwarded
    to resolve_chat_endpoint() as if it were an explicit, pinned value — doing
    so makes the resolver skip its own SBOM/live-probe/stale-candidate-retry
    discovery entirely (see self._chat_path_explicit in __init__)."""
    sbom = AiSbomDocument(target="./app", nodes=[], edges=[])
    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:8080",
        chat_path="",
        chat_payload_key="message",
        chat_payload_list=False,
    )
    # __init__'s legacy SBOM fallback should have set a plain guess here, but
    # it must NOT be treated as explicit input.
    assert orchestrator._chat_path_explicit is False

    resolver = AsyncMock(
        return_value=ResolvedEndpoint(
            path="/extract",
            payload=PayloadShape(key="text", is_list=False, source=EndpointSource.PROBE),
            path_source=EndpointSource.PROBE,
        )
    )
    monkeypatch.setattr(
        "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
        resolver,
    )

    await orchestrator._maybe_probe_endpoints()

    resolver.assert_awaited_once()
    _, kwargs = resolver.call_args
    assert kwargs["endpoint"] is UNSET
    assert orchestrator.resolved_chat_path == "/extract"


@pytest.mark.asyncio
async def test_redteam_maybe_probe_passes_explicit_endpoint_through(monkeypatch) -> None:
    """When the caller DID configure chat_path explicitly, it must still be
    forwarded to resolve_chat_endpoint() as the pinned value (never UNSET)."""
    sbom = AiSbomDocument(target="./app", nodes=[], edges=[])
    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:8080",
        chat_path="/api/agent/chat",
        chat_payload_key="message",
        chat_payload_list=False,
    )
    assert orchestrator._chat_path_explicit is True

    resolver = AsyncMock(
        return_value=ResolvedEndpoint(
            path="/api/agent/chat",
            payload=PayloadShape(key="message", is_list=False, source=EndpointSource.CONFIG),
            path_source=EndpointSource.CONFIG,
        )
    )
    monkeypatch.setattr(
        "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
        resolver,
    )

    await orchestrator._maybe_probe_endpoints()

    resolver.assert_awaited_once()
    _, kwargs = resolver.call_args
    assert kwargs["endpoint"] == "/api/agent/chat"
