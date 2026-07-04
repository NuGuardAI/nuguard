"""Redteam endpoint precedence regressions."""
from __future__ import annotations

import uuid

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
