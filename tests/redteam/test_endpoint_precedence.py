"""Redteam endpoint precedence regressions."""
from __future__ import annotations

import uuid
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
