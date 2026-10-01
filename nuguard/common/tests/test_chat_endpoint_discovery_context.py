"""Tests for the generic chat-endpoint discovery helpers.

Covers :func:`nuguard.common.response_extraction.chat_fitness`,
:mod:`nuguard.common.endpoint_detection.context` (API origin + login
detection before live probing), and the resolver's handling of templated
SBOM paths / deferred probing.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from nuguard.common.auth import AuthConfig
from nuguard.common.endpoint_detection.context import (
    auth_requires_login,
    clear_api_origin_cache,
    resolve_api_origin,
)
from nuguard.common.endpoint_detection.resolver import resolve_chat_endpoint
from nuguard.common.response_extraction import (
    CHAT_FITNESS_NONE,
    CHAT_FITNESS_PROSE,
    CHAT_FITNESS_STRUCTURED,
    CHAT_FITNESS_TERSE,
    chat_fitness,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


def _node(path: str, method: str = "POST", **meta: object) -> Node:
    return Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{method}{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(endpoint=path, method=method, **meta),  # type: ignore[arg-type]
    )


# ── chat_fitness ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "response",
    ["", "   ", "[HTTP 500]", "[CONFIG_ERROR: unresolved path param 'id']", "[REQUEST_ERROR: boom]"],
)
def test_chat_fitness_unusable(response: str) -> None:
    assert chat_fitness(response) == CHAT_FITNESS_NONE


def test_chat_fitness_prose() -> None:
    assert chat_fitness("Hi! How can I help you study today?") == CHAT_FITNESS_PROSE


def test_chat_fitness_terse() -> None:
    assert chat_fitness("OK") == CHAT_FITNESS_TERSE


def test_chat_fitness_structured_json_text() -> None:
    body = '{"modules": [{"title": "Intro", "steps": [{"n": 1}, {"n": 2}]}]}'
    assert chat_fitness(body) == CHAT_FITNESS_STRUCTURED


def test_chat_fitness_structured_raw_overrides_extracted_prose() -> None:
    raw = {"id": "lp1", "description": "A plan", "modules": [{"steps": [{"n": 1}]}]}
    assert chat_fitness("A structured learning plan for you", raw) == CHAT_FITNESS_STRUCTURED


def test_chat_fitness_shallow_chat_envelope_is_prose() -> None:
    raw = {"id": "m1", "role": "assistant", "content": "Hello there", "sources": []}
    assert chat_fitness("Hello there, how can I help?", raw) == CHAT_FITNESS_PROSE


# ── resolve_api_origin ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_api_origin_uses_bundle_origin_and_memoises() -> None:
    clear_api_origin_cache()
    mock = AsyncMock(return_value=("http://app.test:3010", ["baked-in API origin"]))
    with patch(
        "nuguard.common.endpoint_detection.frontend_origin.discover_api_origin_from_frontend_bundle",
        new=mock,
    ):
        first = await resolve_api_origin("http://app.test", None)
        second = await resolve_api_origin("http://app.test", None)

    assert first == ("http://app.test:3010", ["baked-in API origin"])
    assert second == first
    assert mock.await_count == 1
    clear_api_origin_cache()


@pytest.mark.asyncio
async def test_resolve_api_origin_unchanged_when_no_bundle_origin() -> None:
    clear_api_origin_cache()
    with patch(
        "nuguard.common.endpoint_detection.frontend_origin.discover_api_origin_from_frontend_bundle",
        new=AsyncMock(return_value=(None, [])),
    ):
        origin, notes = await resolve_api_origin("http://api.test/", None)
    assert origin == "http://api.test"
    assert notes == []
    clear_api_origin_cache()


# ── auth_requires_login ──────────────────────────────────────────────────────


def _sbom_with_login() -> AiSbomDocument:
    return AiSbomDocument(
        target="./app",
        nodes=[
            _node(
                "/api/v1/auth/login",
                request_body_schema={"email": "string", "password": "string"},
            )
        ],
    )


def test_auth_requires_login_for_basic_with_sbom_login_endpoint() -> None:
    auth = AuthConfig(type="basic", username="a@b.test", password="pw")
    assert auth_requires_login(auth, _sbom_with_login()) is True


def test_auth_requires_login_false_for_plain_basic_without_login_endpoint() -> None:
    auth = AuthConfig(type="basic", username="a@b.test", password="pw")
    assert auth_requires_login(auth, AiSbomDocument(target="./app", nodes=[])) is False


def test_auth_requires_login_false_for_bearer_and_none() -> None:
    assert auth_requires_login(AuthConfig(type="bearer", header="Authorization: Bearer x"), None) is False
    assert auth_requires_login(None, None) is False


# ── resolve_chat_endpoint ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolver_skips_literal_probe_for_templated_sbom_path() -> None:
    chat = "/api/v1/chat/conversations/:id/messages"
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            _node(
                chat,
                chat_payload_key="content",
                path_params=["id"],
                path_param_sources={"id": "/api/v1/chat/conversations"},
            ),
            _node("/api/v1/chat/conversations"),
        ],
    )
    with (
        patch(
            "nuguard.common.endpoint_detection.resolver.detect_payload_shape",
            new=AsyncMock(side_effect=AssertionError("must not probe templated path")),
        ),
        patch(
            "nuguard.common.endpoint_detection.resolver.probe_endpoint",
            new=AsyncMock(side_effect=AssertionError("must not blind-probe")),
        ),
    ):
        resolved = await resolve_chat_endpoint("http://api.test", sbom)

    assert resolved.path == chat
    assert resolved.payload_key == "content"


@pytest.mark.asyncio
async def test_resolver_defers_live_probe_when_disallowed() -> None:
    probe = AsyncMock(side_effect=AssertionError("must not probe"))
    with patch("nuguard.common.endpoint_detection.resolver.probe_endpoint", new=probe):
        resolved = await resolve_chat_endpoint(
            "http://api.test", AiSbomDocument(target="./app", nodes=[]), allow_live_probe=False
        )
    assert resolved.path is None
    assert any("deferred" in n for n in resolved.notes)


@pytest.mark.asyncio
async def test_resolver_replaces_sbom_guess_when_validation_fails() -> None:
    from nuguard.common.endpoint_detection.live_probe import ProbeResult
    from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape

    sbom = AiSbomDocument(target="./app", nodes=[_node("/api/chat/message")])
    fallback_shape = PayloadShape(
        key="message", is_list=False, source=EndpointSource.FALLBACK,
    )
    with (
        patch(
            "nuguard.common.endpoint_detection.resolver.discover_chat_config",
            return_value=("/api/chat/message", "message", False, None),
        ),
        patch(
            "nuguard.common.endpoint_detection.resolver.detect_payload_shape",
            new=AsyncMock(return_value=fallback_shape),
        ),
        patch(
            "nuguard.common.endpoint_detection.resolver.probe_endpoint",
            new=AsyncMock(return_value=ProbeResult("/extract", "text", False)),
        ) as probe,
    ):
        resolved = await resolve_chat_endpoint(
            "http://api.test", sbom, probe_payload_extras={"consumerID": "c1"},
        )

    assert resolved.path == "/extract"
    assert resolved.path_source.value == "probe"
    assert resolved.payload_key == "text"
    assert probe.await_count == 1


@pytest.mark.asyncio
async def test_resolver_uses_browser_after_unconfirmed_http_discovery() -> None:
    from nuguard.common.endpoint_detection.live_probe import ProbeResult
    from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape

    sbom = AiSbomDocument(target="./app", nodes=[_node("/api/chat/message")])
    fallback_shape = PayloadShape(key="message", is_list=False, source=EndpointSource.FALLBACK)
    browser_shape = PayloadShape(key="text", is_list=False, source=EndpointSource.BROWSER)
    browser_auth = AuthConfig(type="basic", username="alice", password="password")
    with (
        patch(
            "nuguard.common.endpoint_detection.resolver.discover_chat_config",
            return_value=("/api/chat/message", "message", False, None),
        ),
        patch(
            "nuguard.common.endpoint_detection.resolver.detect_payload_shape",
            new=AsyncMock(return_value=fallback_shape),
        ),
        patch(
            "nuguard.common.endpoint_detection.resolver.probe_endpoint",
            new=AsyncMock(return_value=ProbeResult("/api/chat/message", "message", False, confirmed=False)),
        ),
        patch(
            "nuguard.common.endpoint_detection.resolver.detect_with_browser",
            new=AsyncMock(return_value=("/extract", browser_shape)),
        ) as browser,
    ):
        resolved = await resolve_chat_endpoint(
            "http://api.test",
            sbom,
            probe_payload_extras={"consumerID": "c1"},
            enable_browser_fallback=True,
            browser_auth_config=browser_auth,
        )

    assert browser.await_args.kwargs["auth_config"] is browser_auth
    assert resolved.path == "/extract"
    assert resolved.path_source is EndpointSource.BROWSER


# ── capability discovery stops when the circuit breaker trips ────────────────


@pytest.mark.asyncio
async def test_capability_discovery_stops_on_target_unavailable() -> None:
    from nuguard.common.discovery import AgentCapabilityGap, run_capability_discovery
    from nuguard.common.errors import TargetUnavailableError

    class _BrokenClient:
        def __init__(self) -> None:
            self.sends = 0

        async def send(self, message: str, session: object = None) -> tuple[str, list[dict]]:
            self.sends += 1
            raise TargetUnavailableError("3 consecutive errors — aborting scan")

    client = _BrokenClient()
    gaps = [
        AgentCapabilityGap(
            agent_id="a1", agent_name="Agent",
            needs_system_prompt=True, needs_tools=True, needs_subagents=True,
        )
    ]

    result = await run_capability_discovery(client, object(), gaps)  # type: ignore[arg-type]

    assert client.sends == 1  # no further probes and no closing turn
    assert result.raw_responses == {}
