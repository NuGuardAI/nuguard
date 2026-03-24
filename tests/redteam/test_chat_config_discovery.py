from __future__ import annotations

from nuguard.redteam.executor.orchestrator import _discover_chat_config
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType


def test_discovery_prefers_chat_message_endpoint_over_chat() -> None:
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
                confidence=0.9,
                metadata={
                    "endpoint": "/chat/message",
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

    assert path == "/chat/message"
    assert payload_key == "message"
    assert payload_list is False
