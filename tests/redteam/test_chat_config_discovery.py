from __future__ import annotations

from nuguard.redteam.executor.orchestrator import _discover_chat_config
from nuguard.sbom.models import AiSbomDocument, Node, ScanSummary
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

    path, payload_key, payload_list, response_key = _discover_chat_config(
        sbom=sbom,
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    assert path == "/api/chat/queue"
    assert payload_key == "message"
    assert payload_list is False
    assert response_key is None  # no response_text_key set on these nodes


def test_langgraph_endpoint_discovered_without_payload_key_in_sbom() -> None:
    """When the SBOM has a LangGraph framework node and a /run_langgraph POST
    endpoint without chat_payload_key set, discovery should infer LangGraph
    conventions (phrases / list=True)."""
    sbom = AiSbomDocument(
        target="healthcare-voice-agent",
        summary=ScanSummary(frameworks=["langgraph", "fastapi"]),
        nodes=[
            Node(
                name="health_check",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.86,
                metadata={"endpoint": "/api/health", "method": "GET"},
            ),
            Node(
                name="run_langgraph",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.86,
                metadata={
                    "endpoint": "/run_langgraph",
                    "method": "POST",
                    # No chat_payload_key — this is the real healthcare SBOM situation
                },
            ),
            Node(
                name="login",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.86,
                metadata={"endpoint": "/login", "method": "POST"},
            ),
        ],
        edges=[],
    )

    path, payload_key, payload_list, response_key = _discover_chat_config(
        sbom=sbom,
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    assert path == "/run_langgraph"
    assert payload_key == "phrases"
    assert payload_list is True
    assert response_key == "prognosis"  # inferred LangGraph convention


def test_langgraph_inference_skipped_without_langgraph_framework() -> None:
    """Without the langgraph framework in the SBOM summary, a /run_langgraph
    endpoint without chat_payload_key must NOT be inferred and should be skipped."""
    sbom = AiSbomDocument(
        target="some-other-app",
        nodes=[
            Node(
                name="run_langgraph",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.86,
                metadata={
                    "endpoint": "/run_langgraph",
                    "method": "POST",
                },
            ),
        ],
        edges=[],
    )

    path, payload_key, payload_list, response_key = _discover_chat_config(
        sbom=sbom,
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    # Falls back to defaults — no LangGraph framework detected
    assert path == "/chat"
    assert payload_key == "message"
    assert payload_list is False
    assert response_key is None


def test_explicit_payload_key_beats_langgraph_inference() -> None:
    """When a node has an explicit chat_payload_key it wins over LangGraph inference."""

    sbom = AiSbomDocument(
        target="hybrid-app",
        summary=ScanSummary(frameworks=["langgraph"]),
        nodes=[
            Node(
                name="run_langgraph",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.86,
                metadata={
                    "endpoint": "/run_langgraph",
                    "method": "POST",
                    "chat_payload_key": "custom_key",
                    "chat_payload_list": False,
                },
            ),
        ],
        edges=[],
    )

    path, payload_key, payload_list, response_key = _discover_chat_config(
        sbom=sbom,
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
    )

    assert path == "/run_langgraph"
    assert payload_key == "custom_key"
    assert payload_list is False
    assert response_key is None  # meta.response_text_key not set
