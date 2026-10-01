"""Regression test: bootstrap failures (None auth session) must not crash."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.endpoint_detection.constants import UNSET
from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape, ResolvedEndpoint
from nuguard.common.errors import TargetEndpointNotFoundError
from nuguard.common.session_resolver import (
    _merge_login_response_extras,
    resolve_target_session,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL
TARGET = "http://app.test"


def test_merge_login_response_extras_handles_no_session() -> None:
    """When bootstrap couldn't establish a session (e.g. user authed the chat
    endpoint directly instead of an SBOM-declared auth endpoint), merging
    must fall back to the static config instead of raising."""
    extras, notes = _merge_login_response_extras(None, {"foo": "bar"})
    assert extras == {"foo": "bar"}
    assert notes == []


def _ws_sbom(path: str = "/ws/chat") -> AiSbomDocument:
    node = Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/WEBSOCKET/{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(endpoint=path, method="WEBSOCKET"),
    )
    return AiSbomDocument(target="./app", nodes=[node])


def _mock_bootstrapper():
    bootstrapper = MagicMock()
    bootstrapper.session.headers.return_value = {}
    bootstrapper.session.login_response_extras.return_value = {}
    health_report = MagicMock()
    health_report.checks = []
    return bootstrapper, health_report


@pytest.mark.asyncio
async def test_resolve_target_session_detects_websocket_from_sbom_before_bootstrap() -> None:
    """A SBOM-declared WEBSOCKET endpoint must be detected before bootstrap runs,
    so bootstrap_auth_runtime() opens a WS handshake instead of an HTTP POST."""
    bootstrapper, health_report = _mock_bootstrapper()
    with patch(
        "nuguard.common.auth_runtime.bootstrap_auth_runtime",
        new=AsyncMock(return_value=(bootstrapper, health_report)),
    ) as mock_bootstrap:
        await resolve_target_session(
            target_url="http://app.test",
            sbom=_ws_sbom(),
            auth_config=None,
            extra_headers={},
            chat_path="",
            chat_payload_key="message",
            chat_payload_list=False,
            chat_payload_extras={},
            chat_response_key=None,
        )
    _, kwargs = mock_bootstrap.call_args
    assert kwargs["is_websocket"] is True
    assert kwargs["endpoint"] == "/ws"

@pytest.mark.asyncio
async def test_unset_endpoint_uses_shared_resolver_before_bootstrap() -> None:
    """The public session path must bootstrap with the shared resolver's
    validated probe result, not independently pin the top SBOM candidate."""
    bootstrapper, health_report = _mock_bootstrapper()
    resolved = ResolvedEndpoint(
        path="/extract",
        payload=PayloadShape(key="text", is_list=False, source=EndpointSource.PROBE),
        path_source=EndpointSource.PROBE,
    )
    with (
        patch(
            "nuguard.common.endpoint_detection.context.resolve_api_origin",
            new=AsyncMock(return_value=(TARGET, [])),
        ),
        patch(
            "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
            new=AsyncMock(return_value=resolved),
        ) as mock_resolve,
        patch(
            "nuguard.common.auth_runtime.bootstrap_auth_runtime",
            new=AsyncMock(return_value=(bootstrapper, health_report)),
        ) as mock_bootstrap,
    ):
        session_cfg, _ = await resolve_target_session(
            target_url=TARGET,
            sbom=AiSbomDocument(target="./app", nodes=[Node(
                name="/api/chat/message",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.9,
                metadata=NodeMetadata(endpoint="/api/chat/message", method="POST"),
            )]),
            auth_config=None,
            extra_headers={},
            chat_path="",
            chat_payload_key="message",
            chat_payload_list=False,
            chat_payload_extras={"consumerID": "c1"},
            chat_response_key=None,
            endpoint_explicit=False,
        )

    _, resolve_kwargs = mock_resolve.call_args
    assert resolve_kwargs["endpoint"] is UNSET
    assert resolve_kwargs["probe_payload_extras"] == {"consumerID": "c1"}
    assert session_cfg.chat_path == "/extract"
    assert session_cfg.chat_payload_key == "text"
    assert session_cfg.endpoint_source == "probe"
    assert mock_bootstrap.call_args.kwargs["endpoint"] == "/extract"


    @pytest.mark.asyncio
    async def test_login_required_endpoint_resolution_is_deferred_until_after_auth() -> None:
        bootstrapper, health_report = _mock_bootstrapper()
        resolved = ResolvedEndpoint(
            path="/extract",
            payload=PayloadShape(key="text", source=EndpointSource.PROBE),
            path_source=EndpointSource.PROBE,
        )
        calls: list[tuple[str, dict[str, str] | None]] = []

        async def _resolve(*_args, **kwargs):
            calls.append(("resolve", kwargs["auth_headers"]))
            return resolved

        async def _bootstrap(**kwargs):
            assert kwargs["endpoint"] == "/chat"
            calls.append(("bootstrap-auth", None))
            await kwargs["endpoint_resolver"]({"Authorization": "Bearer jwt"})
            calls.append(("bootstrap-chat-probe", None))
            return bootstrapper, health_report

        with (
            patch(
                "nuguard.common.endpoint_detection.context.resolve_api_origin",
                new=AsyncMock(return_value=(TARGET, [])),
            ),
            patch(
                "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
                new=AsyncMock(side_effect=_resolve),
            ),
            patch(
                "nuguard.common.auth_runtime.bootstrap_auth_runtime",
                new=AsyncMock(side_effect=_bootstrap),
            ),
        ):
            session_cfg, _ = await resolve_target_session(
                target_url=TARGET,
                sbom=AiSbomDocument(target="./app", nodes=[Node(
                    name="/api/chat/message",
                    component_type=ComponentType.API_ENDPOINT,
                    confidence=0.9,
                    metadata=NodeMetadata(endpoint="/api/chat/message", method="POST"),
                )]),
                auth_config=AuthConfig(
                    type="login_flow",
                    login_flow=LoginFlowConfig(endpoint="/login", payload={"username": "alice"}),
                ),
                extra_headers={},
                chat_path="",
                chat_payload_key="message",
                chat_payload_list=False,
                chat_payload_extras={"consumerID": "c1"},
                chat_response_key=None,
                endpoint_explicit=False,
            )

        assert calls == [
            ("bootstrap-auth", None),
            ("resolve", {"Authorization": "Bearer jwt"}),
            ("bootstrap-chat-probe", None),
        ]
        assert session_cfg.chat_path == "/extract"
        assert session_cfg.chat_payload_key == "text"


    @pytest.mark.asyncio
    async def test_unresolved_endpoint_fails_clearly_without_bootstrapping_chat_default() -> None:
        with (
            patch(
                "nuguard.common.endpoint_detection.context.resolve_api_origin",
                new=AsyncMock(return_value=(TARGET, [])),
            ),
            patch(
                "nuguard.common.endpoint_detection.resolver.resolve_chat_endpoint",
                new=AsyncMock(return_value=ResolvedEndpoint()),
            ),
            patch("nuguard.common.auth_runtime.bootstrap_auth_runtime", new=AsyncMock()) as bootstrap,
        ):
            with pytest.raises(TargetEndpointNotFoundError, match="Could not discover a chat endpoint"):
                await resolve_target_session(
                    target_url=TARGET,
                    sbom=AiSbomDocument(target="./app", nodes=[]),
                    auth_config=None,
                    extra_headers={},
                    chat_path="",
                    chat_payload_key="message",
                    chat_payload_list=False,
                    chat_payload_extras={},
                    chat_response_key=None,
                    endpoint_explicit=False,
                )

        bootstrap.assert_not_awaited()


@pytest.mark.anyio
@respx.mock
async def test_health_report_reflects_discovered_endpoint_not_stale_default() -> None:
    # Issue #532: with no SBOM and no configured endpoint, step 4's bootstrap
    # health check runs against the hardcoded "/chat" default before live
    # probing (step 7) has a chance to discover the real, working endpoint.
    # The returned health_report must reflect the endpoint that will actually
    # be used — not a stale pre-discovery "/chat" 404 — or a working target
    # would still be reported as failed verification.
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{TARGET}/api/agent/chat").mock(
        return_value=httpx.Response(200, json={"response": "hi"})
    )
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))

    session_cfg, health_report = await resolve_target_session(
        target_url=TARGET,
        sbom=None,
        auth_config=None,
        extra_headers={},
        chat_path="",
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
    )

    assert session_cfg.chat_path == "/api/agent/chat"
    assert health_report.endpoint == "/api/agent/chat"
    assert health_report.checks[0].status == "ok"
    assert health_report.all_ok is True


@pytest.mark.asyncio
async def test_websocket_placeholder_endpoint_is_not_re_validated() -> None:
    # Regression guard: the WS-handshake placeholder "/ws" (used deliberately
    # for step 4's bootstrap regardless of the real SBOM-discovered WS path)
    # must not trigger the step-7b re-validation added for the "/chat"
    # default-fallback case above — bootstrap_auth_runtime must be called
    # exactly once.
    bootstrapper, health_report = _mock_bootstrapper()
    with patch(
        "nuguard.common.auth_runtime.bootstrap_auth_runtime",
        new=AsyncMock(return_value=(bootstrapper, health_report)),
    ) as mock_bootstrap:
        await resolve_target_session(
            target_url="http://app.test",
            sbom=_ws_sbom(),
            auth_config=None,
            extra_headers={},
            chat_path="",
            chat_payload_key="message",
            chat_payload_list=False,
            chat_payload_extras={},
            chat_response_key=None,
        )
    assert mock_bootstrap.call_count == 1


@pytest.mark.asyncio
async def test_resolve_target_session_no_websocket_for_plain_http_sbom() -> None:
    from nuguard.sbom.models import Node as _Node
    from nuguard.sbom.models import NodeMetadata as _NodeMetadata

    http_node = _Node(
        id=uuid.uuid5(_NS, "API_ENDPOINT/POST//api/chat"),
        name="/api/chat",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=_NodeMetadata(endpoint="/api/chat", method="POST", chat_payload_key="message"),
    )
    sbom = AiSbomDocument(target="./app", nodes=[http_node])
    bootstrapper, health_report = _mock_bootstrapper()
    with patch(
        "nuguard.common.auth_runtime.bootstrap_auth_runtime",
        new=AsyncMock(return_value=(bootstrapper, health_report)),
    ) as mock_bootstrap:
        await resolve_target_session(
            target_url="http://app.test",
            sbom=sbom,
            auth_config=None,
            extra_headers={},
            chat_path="/api/chat",
            chat_payload_key="custom_key",
            chat_payload_list=False,
            chat_payload_extras={},
            chat_response_key=None,
        )
    _, kwargs = mock_bootstrap.call_args
    assert kwargs["is_websocket"] is False
