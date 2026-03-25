from __future__ import annotations

from nuguard.redteam.executor.orchestrator import _discover_chat_config
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType


def test_discovery_prefers_source_backed_queue_endpoint_over_synthetic_chat_message() -> None:
    sbom = AiSbomDocument(
        target="stateset-icommerce",
        nodes=[
            Node(
                name="Chat API",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.8,
                metadata={
                    "endpoint": "/chat",
                    "method": "POST",
                    "chat_payload_key": "message",
                },
            ),
            Node(
                name="Webchat Message API",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.62,
                metadata={
                    "endpoint": "/chat/message",
                    "method": "POST",
                    "chat_payload_key": "message",
                    "extras": {"source": "auto_enrichment"},
                },
            ),
            Node(
                name="Queue API",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.92,
                metadata={
                    "endpoint": "/api/chat/queue",
                    "method": "POST",
                    "chat_payload_key": "message",
                },
            ),
        ],
        edges=[],
    )

    path, payload_key, payload_list = _discover_chat_config(
        sbom=sbom,
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    assert path == "/api/chat/queue"
    assert payload_key == "message"
    assert payload_list is False
