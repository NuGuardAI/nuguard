"""Unit tests for nuguard/common/endpoint_preflight.py."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from nuguard.common.endpoint_preflight import PreflightOutcome, validate_and_rotate_chat_endpoint
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

if TYPE_CHECKING:
    from nuguard.redteam.target.client import TargetAppClient

_NS = uuid.NAMESPACE_URL


async def _validate(client: Any, sbom: AiSbomDocument, **kwargs: Any) -> PreflightOutcome:
    """Test-only shim: these fakes are structurally, not nominally, TargetAppClient."""
    return await validate_and_rotate_chat_endpoint(cast("TargetAppClient", client), sbom, **kwargs)


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

    outcome = await _validate(
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

    outcome = await _validate(
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

    outcome = await _validate(
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
        outcome = await _validate(
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
        outcome = await _validate(
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

    outcome = await _validate(
        client, sbom, has_explicit_endpoint=False,
    )

    assert outcome.ok is True
    assert outcome.rotated_endpoint is not None
    assert outcome.rotated_endpoint[0] == "/chat"


@pytest.mark.asyncio
async def test_422_also_triggers_rotation() -> None:
    """A Pydantic validation-error 422 is also a wrong-endpoint signal — e.g.
    a domain-specific endpoint (letter generator, etc.) that made it into the
    SBOM candidate list rejects a benign 'Hello' test with 422 listing
    unrelated required fields, not 400/404/405."""
    client = _DummyClient(
        working_path="/chat",
        initial_path="/api/agent/chat",
        failure_response="[HTTP 422] unprocessable entity",
    )
    sbom = _sbom_with_candidates("/chat", "/api/agent/chat")

    outcome = await _validate(
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

    outcome = await _validate(
        client, sbom, has_explicit_endpoint=True,
    )

    assert outcome.ok is False
    assert outcome.rotated_endpoint is None
    assert client.called_paths == ["/chat"]


# ---------------------------------------------------------------------------
# Path-param bootstrap (see docs/redteam-test-fix.md)
# ---------------------------------------------------------------------------


class _BootstrapDummyClient(_DummyClient):
    """Extends _DummyClient with invoke_endpoint/set_path_param so the
    path-param bootstrap step (which needs both) can be exercised."""

    def __init__(
        self,
        *args: object,
        invoke_responses: dict[str, tuple[int, dict]] | None = None,
        chat_response_after_bootstrap: str = "OK",
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.invoke_responses = invoke_responses or {}
        self.invoke_calls: list[tuple[str, dict]] = []
        self.bound_params: dict[str, str] = {}
        self._chat_response_after_bootstrap = chat_response_after_bootstrap
        self._bootstrapped = False

    async def send(self, message: str, session: object) -> tuple[str, list[dict]]:
        self.called_paths.append(self.chat_path)
        if self._bootstrapped:
            return self._chat_response_after_bootstrap, []
        if self.chat_path == self.working_path:
            return "OK", []
        return self.failure_response, []

    async def invoke_endpoint(
        self, path: str, method: str = "POST", body: dict | None = None, **_kw: object
    ) -> tuple[int, str, dict]:
        self.invoke_calls.append((path, body or {}))
        status, data = self.invoke_responses.get(path, (404, {}))
        return status, "", data

    def set_path_param(self, name: str, value: str) -> None:
        self.bound_params[name] = value
        self._bootstrapped = True


def _sbom_with_two_step_chat(
    chat_path: str = "/chat/conversations/:id/messages",
    source_path: str = "/chat/conversations",
    request_body_schema: dict[str, str] | None = None,
) -> AiSbomDocument:
    chat_node = Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{chat_path}"),
        name=chat_path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.99,
        metadata=NodeMetadata(
            endpoint=chat_path,
            method="POST",
            chat_payload_key="content",
            chat_payload_list=False,
            path_params=["id"],
            path_param_sources={"id": source_path},
        ),
    )
    source_node = Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{source_path}"),
        name=source_path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.99,
        metadata=NodeMetadata(
            endpoint=source_path,
            method="POST",
            request_body_schema=request_body_schema or {},
        ),
    )
    return AiSbomDocument(target="./app", nodes=[chat_node, source_node])


@pytest.mark.asyncio
async def test_bootstrap_binds_path_param_on_successful_endpoint() -> None:
    chat_path = "/chat/conversations/:id/messages"
    sbom = _sbom_with_two_step_chat(chat_path=chat_path)
    client = _BootstrapDummyClient(
        working_path=chat_path,
        initial_path=chat_path,
        failure_response="[CONFIG_ERROR: unresolved path param 'id']",
        invoke_responses={"/chat/conversations": (201, {"id": "c_abc123"})},
    )

    outcome = await _validate(
        client, sbom, has_explicit_endpoint=False,
    )

    assert outcome.ok is True
    assert client.bound_params == {"id": "c_abc123"}
    assert any("Bootstrapped path param 'id'" in n for n in outcome.notes)


@pytest.mark.asyncio
async def test_bootstrap_falls_back_to_schema_payload_on_4xx() -> None:
    chat_path = "/chat/conversations/:id/messages"
    sbom = _sbom_with_two_step_chat(
        chat_path=chat_path, request_body_schema={"title": "str"}
    )
    client = _BootstrapDummyClient(
        working_path=chat_path,
        initial_path=chat_path,
        failure_response="[CONFIG_ERROR: unresolved path param 'id']",
        invoke_responses={"/chat/conversations": (201, {"id": "c_xyz789"})},
    )

    # The dummy only has one canned response per path; simulate the
    # "first call (empty body) fails, needs schema payload" case directly.
    async def _invoke(path: str, method: str = "POST", body: dict | None = None, **_kw: object):
        client.invoke_calls.append((path, body or {}))
        if not body:
            return 400, "", {}
        return 201, "", {"id": "c_xyz789"}

    client.invoke_endpoint = _invoke  # type: ignore[method-assign]

    outcome = await _validate(
        client, sbom, has_explicit_endpoint=False,
    )

    assert outcome.ok is True
    assert client.bound_params == {"id": "c_xyz789"}
    # Second invoke call must have used the schema-derived non-empty body.
    assert client.invoke_calls[-1][1] == {"title": "test-value"}


@pytest.mark.asyncio
async def test_bootstrap_leaves_param_unbound_when_source_post_fails() -> None:
    chat_path = "/chat/conversations/:id/messages"
    sbom = _sbom_with_two_step_chat(chat_path=chat_path)
    client = _BootstrapDummyClient(
        working_path=chat_path,
        initial_path=chat_path,
        failure_response="[CONFIG_ERROR: unresolved path param 'id']",
        invoke_responses={"/chat/conversations": (500, {})},
        chat_response_after_bootstrap="[CONFIG_ERROR: unresolved path param 'id']",
    )

    outcome = await _validate(
        client, sbom, has_explicit_endpoint=False,
    )

    # Best-effort: bootstrap failure does not force ok=False.
    assert outcome.ok is True
    assert client.bound_params == {}
    assert not any("Bootstrapped path param" in n for n in outcome.notes)


@pytest.mark.asyncio
async def test_no_path_param_sources_is_a_no_op() -> None:
    """Regression guard: an endpoint with no path_param_sources sees no
    behavior change (no invoke_endpoint call attempted)."""
    client = _DummyClient(working_path="/chat")
    sbom = _sbom_with_candidates("/chat")

    outcome = await _validate(
        client, sbom, has_explicit_endpoint=False,
    )

    assert outcome.ok is True
    assert not any("Bootstrapped path param" in n for n in outcome.notes)


@pytest.mark.asyncio
async def test_bootstrap_runs_after_probe_rotation_settles() -> None:
    """Bootstrap must bind against the *rotated* endpoint, not the original
    one — set_chat_endpoint clears any previously-bound path params, so
    binding before rotation settles would be silently wiped. Exercised via
    the live-probe rotation branch (SBOM-candidate rotation only fires when
    discover_chat_candidates_from_sbom ranks a second candidate, which a
    single two-step chat endpoint alone doesn't produce)."""
    chat_path = "/chat/conversations/:id/messages"
    other_path = "/api/agent/chat"
    sbom = _sbom_with_two_step_chat(chat_path=chat_path)
    client = _BootstrapDummyClient(
        working_path=chat_path,
        initial_path=other_path,
        failure_response="[HTTP 404] not found",
        invoke_responses={"/chat/conversations": (201, {"id": "c_rot123"})},
    )

    with patch(
        "nuguard.common.endpoint_probe.probe_chat_endpoints",
        new=AsyncMock(return_value=(chat_path, "content", False)),
    ):
        outcome = await _validate(
            client, sbom, has_explicit_endpoint=False,
        )

    assert outcome.ok is True
    assert outcome.endpoint_source == "probe"
    assert client.bound_params == {"id": "c_rot123"}
