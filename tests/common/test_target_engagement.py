"""Tests for generic login + agent-engagement robustness.

Regression coverage for the OWASP Juice Shop redteam run where every probe
got ``{"error": "... messages must not be empty"}`` and the scan still
"passed":

* the live probe ranks error-envelope fallbacks by what the error says, and
  follows field names the app names in its own error;
* unconfirmed probe fallbacks are never saved to the SBOM as confirmed;
* bootstrap probes with the resolved payload shape, reads SSE bodies, and
  reports error-only replies as ``engagement_error`` (no browser recovery);
* basic credentials are upgraded to a login flow by trying schema-less login
  routes live, including nested ``authentication.token`` responses;
* the SBOM enricher infers ``messages`` for a chat endpoint fronting a
  messages-array LLM SDK call;
* browser-login token extraction and redteam engagement helpers.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from nuguard.common.auth import AuthConfig
from nuguard.common.auto_sbom_enricher import persist_probe_result_to_sbom
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.common.browser_login.heuristics import (
    bearer_from_authorization_header,
    extract_storage_token,
)
from nuguard.common.endpoint_detection.live_probe import (
    ProbeResult,
    _classify_error_envelope,
    _error_field_hints,
    probe_chat_endpoints,
)
from nuguard.common.target_client_builder import discover_login_flow_live
from nuguard.common.transport import error_envelope_message, error_only_response
from nuguard.redteam.executor.orchestrator import (
    _first_error_only_response,
    _record_is_error_only,
)
from nuguard.sbom.enricher import _enrich_chat_payload_from_llm_calls
from nuguard.sbom.models import AiSbomDocument, Evidence, Node, NodeMetadata, SourceLocation
from nuguard.sbom.types import ComponentType

TARGET = "http://target.test"
SHAPE_ERROR = {"error": "LLM error: AI_InvalidPromptError: Invalid prompt: messages must not be empty"}
BACKEND_ERROR = {
    "error": "LLM error: AI_RetryError: Failed after 3 attempts. Last error: "
    "Cannot connect to API: connect ECONNREFUSED 127.0.0.1:11434"
}


def _sse(*events: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _endpoint(path: str, **meta: object) -> Node:
    return Node(
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.5,
        metadata=NodeMetadata(endpoint=path, method="POST", **meta),
    )


# ── Error-envelope classification ────────────────────────────────────────────


def test_classify_error_envelope_distinguishes_shape_from_backend() -> None:
    assert _classify_error_envelope(SHAPE_ERROR) == "shape_rejected"
    assert _classify_error_envelope(BACKEND_ERROR) == "backend_error"
    assert _classify_error_envelope({"error": "something odd"}) == "unknown"


def test_error_field_hints_extracts_named_fields() -> None:
    assert _error_field_hints({"error": "`question` is required"}) == ["question"]
    assert _error_field_hints({"detail": "missing field: query_text"}) == ["query_text"]
    # "prompt" in "Invalid prompt: messages must not be empty" is not a field.
    assert _error_field_hints(SHAPE_ERROR) == ["messages"]


# ── Live probe ───────────────────────────────────────────────────────────────


def _messages_route(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if isinstance(body.get("messages"), list):
        return _sse(BACKEND_ERROR)
    return _sse(SHAPE_ERROR)


@pytest.mark.anyio
@respx.mock
async def test_probe_prefers_shape_that_reached_the_backend() -> None:
    # Every shape errors (the app's LLM backend is down), but only the
    # "messages" list got past validation — it must be the fallback, not the
    # first shape tried, and it must be marked unconfirmed.
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{TARGET}/rest/chat").mock(side_effect=_messages_route)

    result = await probe_chat_endpoints(TARGET, sbom=None, hint_path="/rest/chat")

    assert result is not None
    assert (result.key, result.is_list) == ("messages", True)
    assert result.confirmed is False


@pytest.mark.anyio
@respx.mock
async def test_probe_follows_field_named_in_error_envelope() -> None:
    def route(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "question" in body:
            return httpx.Response(200, json={"answer": "hi"})
        return httpx.Response(200, json={"error": "`question` is required"})

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{TARGET}/ask").mock(side_effect=route)

    result = await probe_chat_endpoints(TARGET, sbom=None, hint_path="/ask")

    assert result is not None
    assert result.key == "question"
    assert result.confirmed is True


# ── Probe persistence ────────────────────────────────────────────────────────


def test_unconfirmed_probe_result_clears_stale_confirmation(tmp_path: Path) -> None:
    stale = _endpoint(
        "/rest/chat",
        chat_payload_key="message",
        extras={"source": "runtime_probe", "confirmed_at": datetime.now(timezone.utc).isoformat()},
    )
    sbom = AiSbomDocument(target="./app", nodes=[stale], edges=[])
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text("{}")

    persist_probe_result_to_sbom(
        ProbeResult("/rest/chat", "messages", True, confirmed=False), sbom, sbom_path
    )

    assert stale.metadata.chat_payload_key == "message"  # not overwritten
    assert "source" not in stale.metadata.extras
    raw = json.loads((tmp_path / "app.sbom.enriched.json").read_text())
    raw.pop("_enrichment_cache_key", None)
    node = AiSbomDocument.model_validate(raw).nodes[0]
    assert (node.metadata.extras or {}).get("source") != "runtime_probe"


# ── Transport helpers ────────────────────────────────────────────────────────


def test_error_only_response_variants() -> None:
    assert error_only_response(json.dumps([SHAPE_ERROR])).startswith("LLM error")
    assert error_only_response(json.dumps(SHAPE_ERROR)).startswith("LLM error")
    assert error_only_response(f"data: {json.dumps(BACKEND_ERROR)}\n\ndata: [DONE]\n")
    assert error_only_response("```json\n" + json.dumps(SHAPE_ERROR) + "\n```")
    assert error_only_response("Hello! How can I help?") == ""
    assert error_only_response(json.dumps({"message": "Hi there"})) == ""
    assert error_only_response(json.dumps([SHAPE_ERROR, {"choices": [{"delta": {"content": "x"}}]}])) == ""
    assert error_envelope_message({"error": {"message": "bad"}}) == "bad"


# ── Bootstrap ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_bootstrap_uses_payload_shape_and_flags_sse_errors() -> None:
    route = respx.post(f"{TARGET}/rest/chat").mock(return_value=_sse(BACKEND_ERROR))
    bootstrapper = AuthBootstrapper(
        target_url=TARGET,
        endpoint="/rest/chat",
        default_auth=AuthConfig(type="none"),
        startup_retries=0,
        payload_key="messages",
        payload_list=True,
    )

    report = await bootstrapper.run()

    sent = json.loads(route.calls[0].request.content)
    assert sent["messages"] == [{"role": "user", "content": "Hello, how can you help me?"}]
    check = report.checks[0]
    assert check.status == "ok"
    assert "ECONNREFUSED" in check.engagement_error
    assert check.body_warning == ""  # SSE is not "invalid JSON"


@pytest.mark.anyio
@respx.mock
async def test_bootstrap_sentinel_payload_key_keeps_message_probe() -> None:
    route = respx.post(f"{TARGET}/run").mock(return_value=httpx.Response(200, json={"response": "hi"}))
    bootstrapper = AuthBootstrapper(
        target_url=TARGET, endpoint="/run", startup_retries=0, payload_key="__adk__", payload_list=True,
    )

    await bootstrapper.run()

    assert json.loads(route.calls[0].request.content)["message"] == "Hello, how can you help me?"


# ── Live login discovery ─────────────────────────────────────────────────────


@pytest.mark.anyio
@respx.mock
async def test_discover_login_flow_live_finds_nested_token() -> None:
    def login(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("email") == "a@b.test" and body.get("password") == "pw":
            return httpx.Response(200, json={"authentication": {"token": "eyJ.abc.def", "umail": "a@b.test"}})
        return httpx.Response(401, json={"error": "Invalid email or password."})

    respx.post(f"{TARGET}/rest/user/login").mock(side_effect=login)
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))
    sbom = AiSbomDocument(target="./app", nodes=[_endpoint("/rest/user/login")], edges=[])

    upgraded, note = await discover_login_flow_live(
        TARGET, AuthConfig(type="basic", username="a@b.test", password="pw"), sbom
    )

    assert upgraded is not None and upgraded.login_flow is not None
    assert upgraded.login_flow.endpoint == "/rest/user/login"
    assert upgraded.login_flow.token_response_key == "authentication.token"
    assert upgraded.login_flow.payload == {"email": "a@b.test", "password": "pw"}
    assert note and "pw" not in note  # never echo the password


@pytest.mark.anyio
@respx.mock
async def test_discover_login_flow_live_returns_none_without_token() -> None:
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))

    upgraded, note = await discover_login_flow_live(
        TARGET, AuthConfig(type="basic", username="u", password="p"), None
    )

    assert (upgraded, note) == (None, None)


# ── SBOM enrichment ──────────────────────────────────────────────────────────


def _agent_with_stream_text(path: str) -> Node:
    return Node(
        name="Chat Assistant",
        component_type=ComponentType.AGENT,
        confidence=0.75,
        evidence=[
            Evidence(
                kind="ast_call",
                confidence=0.75,
                detail="vercel_ai_sdk_ts: streamText({\n  model: provider(model),\n  messages,\n",
                location=SourceLocation(path=path, line=203),
            )
        ],
    )


def test_enricher_infers_messages_key_for_chat_endpoint() -> None:
    chat = _endpoint("/rest/chat")
    other = _endpoint("/api/Users")
    doc = AiSbomDocument(target="./app", nodes=[_agent_with_stream_text("routes/chat.ts"), chat, other], edges=[])

    _enrich_chat_payload_from_llm_calls(doc)

    assert (chat.metadata.chat_payload_key, chat.metadata.chat_payload_list) == ("messages", True)
    assert other.metadata.chat_payload_key is None


def test_enricher_keeps_existing_chat_key_and_skips_ambiguous() -> None:
    configured = _endpoint("/chat", chat_payload_key="prompt")
    doc = AiSbomDocument(target="./app", nodes=[_agent_with_stream_text("lib/llm.ts"), configured], edges=[])
    _enrich_chat_payload_from_llm_calls(doc)
    assert configured.metadata.chat_payload_key == "prompt"

    a, b = _endpoint("/chat"), _endpoint("/agent/ask")
    doc = AiSbomDocument(target="./app", nodes=[_agent_with_stream_text("lib/llm.ts"), a, b], edges=[])
    _enrich_chat_payload_from_llm_calls(doc)
    assert a.metadata.chat_payload_key is None and b.metadata.chat_payload_key is None


# ── Browser-login token capture ──────────────────────────────────────────────


def test_extract_storage_token_prefers_jwt() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    assert extract_storage_token({"language": "en", "token": jwt}) == jwt
    assert extract_storage_token({"auth": json.dumps({"accessToken": jwt})}) == jwt
    assert extract_storage_token({"session_token": "opaque-0123456789abcdef"}) == "opaque-0123456789abcdef"
    assert extract_storage_token({"theme": "dark", "welcomebanner_status": "dismiss"}) is None
    assert bearer_from_authorization_header("Bearer abc") == "abc"
    assert bearer_from_authorization_header("Basic xyz") is None


# ── Redteam engagement helpers ───────────────────────────────────────────────


class _Record:
    def __init__(self, responses: list[str]) -> None:
        self.steps = [{"response": r} for r in responses]


def test_record_is_error_only() -> None:
    assert _record_is_error_only(_Record([json.dumps([SHAPE_ERROR]), json.dumps(SHAPE_ERROR)]))
    assert _first_error_only_response(_Record([json.dumps([SHAPE_ERROR])])).startswith("LLM error")
    assert not _record_is_error_only(_Record([json.dumps([SHAPE_ERROR]), "Sure, here you go."]))
    assert not _record_is_error_only(_Record([]))


def test_extract_login_token_exact_nested_and_miss() -> None:
    from nuguard.common.auth import extract_login_token

    assert extract_login_token({"access_token": "a"}, "access_token") == ("access_token", "a")
    assert extract_login_token({"authentication": {"token": "t"}}, "access_token") == (
        "authentication.token",
        "t",
    )
    assert extract_login_token({"ok": True}, "token") is None
    assert extract_login_token(["not", "a", "dict"], "token") is None
