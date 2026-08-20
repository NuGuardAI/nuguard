"""Unit tests for nuguard/common/endpoint_preflight.py."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from nuguard.common.endpoint_preflight import validate_and_rotate_chat_endpoint
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


class _DummyClient:
    """Minimal TargetAppClient stand-in — mirrors the fake used in behavior tests."""

    def __init__(
        self, working_path: str | None, initial_path: str = "/chat",
        failure_response: str = "[HTTP 404] not found",
    ) -> None:
        self.chat_path = initial_path
        self.working_path = working_path
        self.called_paths: list[str] = []
        self.failure_response = failure_response

    async def send(self, message: str, session: object) -> tuple[str, list[dict]]:
        self.called_paths.append(self.chat_path)
        if self.chat_path == self.working_path:
            return "OK", []
        return self.failure_response, []

    def set_chat_endpoint(
        self,
        chat_path: str,
        chat_payload_key: str,
        chat_payload_list: bool,
        chat_response_key: str | None = None,
    ) -> None:
        self.chat_path = chat_path


def _sbom_with_candidates(*paths: str) -> AiSbomDocument:
    nodes = [
        Node(
            id=uuid.uuid5(_NS, f"API_ENDPOINT/{p}"),
            name=p,
            component_type=ComponentType.API_ENDPOINT,
            confidence=0.99,
            metadata=NodeMetadata(
                endpoint=p,
                method="POST",
                chat_payload_key="message",
                chat_payload_list=False,
            ),
        )
        for p in paths
    ]
    return AiSbomDocument(target="./app", nodes=nodes)


@pytest.mark.asyncio
async def test_ok_when_first_response_is_not_404_405() -> None:
    client = _DummyClient(working_path="/chat")
    sbom = _sbom_with_candidates("/chat")

    outcome = await validate_and_rotate_chat_endpoint(
        client, sbom, has_explicit_endpoint=False,
    )

    assert outcome.ok is True
    assert outcome.rotated_endpoint is None
    assert client.called_paths == ["/chat"]


@pytest.mark.asyncio
async def test_rotates_to_working_sbom_candidate_on_404() -> None:
    # SBOM scoring ranks "/api/agent/chat" above "/chat" (startswith("/api/") bonus);
    # simulate that the caller already tried the top-ranked candidate and it 404s,
    # so rotation should fall through to the next-ranked candidate, "/chat".
    client = _DummyClient(working_path="/chat", initial_path="/api/agent/chat")
    sbom = _sbom_with_candidates("/chat", "/api/agent/chat")

    outcome = await validate_and_rotate_chat_endpoint(
        client, sbom, has_explicit_endpoint=False,
    )

    assert outcome.ok is True
    assert outcome.endpoint_source == "sbom"
    assert outcome.rotated_endpoint is not None
    assert outcome.rotated_endpoint[0] == "/chat"
    assert client.chat_path == "/chat"


@pytest.mark.asyncio
async def test_explicit_endpoint_does_not_rotate() -> None:
    client = _DummyClient(working_path="/api/agent/chat")
    sbom = _sbom_with_candidates("/chat", "/api/agent/chat")

    outcome = await validate_and_rotate_chat_endpoint(
        client, sbom, has_explicit_endpoint=True,
    )

    assert outcome.ok is False
    assert outcome.rotated_endpoint is None
    assert client.called_paths == ["/chat"]
    assert any("Explicit endpoint precedence" in n for n in outcome.notes)


@pytest.mark.asyncio
async def test_falls_back_to_live_probe_when_no_sbom_candidate_works() -> None:
    client = _DummyClient(working_path="/v2/chat", initial_path="/api/agent/chat")
    sbom = _sbom_with_candidates("/chat", "/api/agent/chat")

    with patch(
        "nuguard.common.endpoint_probe.probe_chat_endpoints",
        new=AsyncMock(return_value=("/v2/chat", "message", False)),
    ):
        outcome = await validate_and_rotate_chat_endpoint(
            client, sbom, has_explicit_endpoint=False,
        )

    assert outcome.ok is True
    assert outcome.endpoint_source == "probe"
    assert outcome.rotated_endpoint == ("/v2/chat", "message", False, None)
    assert client.chat_path == "/v2/chat"


@pytest.mark.asyncio
async def test_reports_failure_when_nothing_works() -> None:
    client = _DummyClient(working_path=None)
    sbom = _sbom_with_candidates("/chat", "/api/agent/chat")

    with patch(
        "nuguard.common.endpoint_probe.probe_chat_endpoints",
        new=AsyncMock(return_value=None),
    ):
        outcome = await validate_and_rotate_chat_endpoint(
            client, sbom, has_explicit_endpoint=False,
        )

    assert outcome.ok is False
    assert outcome.rotated_endpoint is None
    assert any("unreachable" in n for n in outcome.notes)


@pytest.mark.asyncio
async def test_400_also_triggers_rotation() -> None:
    """A validation-error 400 (not just 404/405) is also a wrong-endpoint
    signal — e.g. an image-upload route auto-selected over the real chat
    endpoint rejects a benign 'Hello' test with 400, not 404/405."""
    client = _DummyClient(
        working_path="/chat",
        initial_path="/api/agent/chat",
        failure_response="[HTTP 400] bad request",
    )
    sbom = _sbom_with_candidates("/chat", "/api/agent/chat")

    outcome = await validate_and_rotate_chat_endpoint(
        client, sbom, has_explicit_endpoint=False,
    )

    assert outcome.ok is True
    assert outcome.rotated_endpoint is not None
    assert outcome.rotated_endpoint[0] == "/chat"


@pytest.mark.asyncio
async def test_400_on_explicit_endpoint_reports_failure_without_rotating() -> None:
    client = _DummyClient(
        working_path="/api/agent/chat",
        initial_path="/chat",
        failure_response="[HTTP 400] bad request",
    )
    sbom = _sbom_with_candidates("/chat", "/api/agent/chat")

    outcome = await validate_and_rotate_chat_endpoint(
        client, sbom, has_explicit_endpoint=True,
    )

    assert outcome.ok is False
    assert outcome.rotated_endpoint is None
    assert client.called_paths == ["/chat"]
