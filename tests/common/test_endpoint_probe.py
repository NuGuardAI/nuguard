"""Tests for endpoint discovery precedence in nuguard.common.endpoint_probe."""
from __future__ import annotations

import uuid

from nuguard.common.endpoint_probe import discover_chat_config_from_sbom
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


def test_discover_chat_config_preserves_explicit_endpoint() -> None:
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            _endpoint_node("/api/chat/message", key="message", payload_list=False),
            _endpoint_node("/api/agent/chat", key="phrases", payload_list=True),
        ],
        edges=[],
    )

    path, key, payload_list, response_key = discover_chat_config_from_sbom(
        sbom,
        chat_path="/api/agent/chat",
        chat_payload_key="phrases",
        chat_payload_list=True,
    )

    assert path == "/api/agent/chat"
    assert key == "phrases"
    assert payload_list is True
    assert response_key is None


def test_discover_chat_config_uses_sbom_when_endpoint_unset() -> None:
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            _endpoint_node("/api/chat/message", key="message", payload_list=False),
            _endpoint_node("/api/agent/chat", key="phrases", payload_list=True),
        ],
        edges=[],
    )

    path, key, payload_list, _ = discover_chat_config_from_sbom(
        sbom,
        chat_path="",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    assert path in {"/api/chat/message", "/api/agent/chat"}
    if path == "/api/chat/message":
        assert key == "message"
        assert payload_list is False
    else:
        assert key == "phrases"
        assert payload_list is True
