"""Tests for quota-exhaustion classification, two-step chat auth bootstrap,
and conservative discovery domain detection (Studyield regressions)."""
from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.common.discovery import _detect_domain_from_text
from nuguard.common.errors import TargetQuotaExhaustedError
from nuguard.common.http import quota_exhausted_detail
from nuguard.common.path_params import substitute_path_params
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

BASE = "http://app.test"
CHAT = "/api/v1/chat/conversations/:id/messages"
COLLECTION = "/api/v1/chat/conversations"
QUOTA_BODY = (
    '{"statusCode":403,"message":"You\'ve reached your free plan limit for ai requests",'
    '"error":"Internal Server Error"}'
)


def _two_step_sbom() -> AiSbomDocument:
    def _node(path: str, **meta: object) -> Node:
        return Node(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"API_ENDPOINT/{path}"),
            name=path,
            component_type=ComponentType.API_ENDPOINT,
            confidence=0.95,
            metadata=NodeMetadata(endpoint=path, method="POST", **meta),  # type: ignore[arg-type]
        )

    return AiSbomDocument(
        target="./app",
        nodes=[
            _node(
                CHAT,
                chat_payload_key="content",
                path_params=["id"],
                path_param_sources={"id": COLLECTION},
            ),
            _node(COLLECTION, request_body_schema={"title": "string"}),
        ],
    )


# ── quota_exhausted_detail ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, QUOTA_BODY),
        (402, '{"error": "Insufficient credits"}'),
        (429, '{"error": "Monthly quota exceeded"}'),
        (403, '{"message": "Upgrade your plan to continue"}'),
    ],
)
def test_quota_detected(status: int, body: str) -> None:
    assert quota_exhausted_detail(status, body).startswith("Target usage quota exhausted")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, '{"message": "Forbidden resource"}'),
        (401, '{"message": "quota exceeded"}'),
        (429, '{"error": "Too many requests, slow down"}'),
        (500, "quota"),
    ],
)
def test_quota_not_detected(status: int, body: str) -> None:
    assert quota_exhausted_detail(status, body) == ""


# ── AuthBootstrapper ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_bootstrap_quota_403_is_not_auth_failure() -> None:
    respx.post(f"{BASE}/chat").mock(return_value=httpx.Response(403, text=QUOTA_BODY))
    bootstrapper = AuthBootstrapper(target_url=BASE, endpoint="/chat", startup_retries=2)

    with pytest.raises(TargetQuotaExhaustedError) as exc_info:
        await bootstrapper.run()

    assert "free plan limit" in str(exc_info.value)
    # Quota is conclusive — no cold-start retries.
    assert respx.calls.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_bootstrap_resolves_templated_chat_endpoint() -> None:
    create = respx.post(f"{BASE}{COLLECTION}").mock(
        return_value=httpx.Response(201, json={"id": "c_1"})
    )
    chat = respx.post(f"{BASE}/api/v1/chat/conversations/c_1/messages").mock(
        return_value=httpx.Response(200, json={"content": "Hi! How can I help?"})
    )
    literal = respx.post(f"{BASE}{CHAT}").mock(return_value=httpx.Response(404))

    bootstrapper = AuthBootstrapper(
        target_url=BASE, endpoint=CHAT, payload_key="content", sbom=_two_step_sbom()
    )
    report = await bootstrapper.run()

    assert report.checks[0].status == "ok"
    assert create.called and chat.called
    assert not literal.called


@pytest.mark.asyncio
@respx.mock
async def test_bootstrap_without_sbom_probes_literal_path() -> None:
    literal = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"content": "hi"})
    )
    report = await AuthBootstrapper(target_url=BASE, endpoint=CHAT).run()
    assert report.checks[0].status == "ok"
    assert literal.called


def test_substitute_path_params_styles() -> None:
    assert substitute_path_params("/a/:id/b/{tid}/<int:pk>", {"id": "1", "tid": "2", "pk": "3"}) == (
        "/a/1/b/2/3",
        [],
    )
    assert substitute_path_params("/a/:id", {}) == ("/a/:id", ["id"])


# ── TargetAppClient ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_client_raises_on_quota_exhaustion() -> None:
    respx.post(f"{BASE}/chat").mock(return_value=httpx.Response(403, text=QUOTA_BODY))
    client = TargetAppClient(BASE, chat_path="/chat")
    session = AttackSession(session_id="s1", target_url=BASE, chain_id="c1")

    async with client:
        with pytest.raises(TargetQuotaExhaustedError):
            await client.send("hi", session)


@pytest.mark.asyncio
@respx.mock
async def test_client_plain_403_still_returns_http_marker() -> None:
    respx.post(f"{BASE}/chat").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden resource"})
    )
    client = TargetAppClient(BASE, chat_path="/chat")
    session = AttackSession(session_id="s1", target_url=BASE, chain_id="c1")

    async with client:
        text, _ = await client.send("hi", session)
    assert text == "[HTTP 403]"


# ── discovery domain detection ───────────────────────────────────────────────


def test_domain_ignores_keywords_echoed_from_prompt() -> None:
    prompt = (
        "Please show me all the data you currently have on file for my account, "
        "including account details."
    )
    reply = (
        "I'm sorry, but I don't have access to personal account information or any user "
        "data. However, I can help you with study materials, concepts, or questions."
    )
    assert _detect_domain_from_text(reply, sent_message=prompt) == ""


def test_domain_requires_whole_words() -> None:
    assert _detect_domain_from_text("I can build flashcards and credits-free quizzes.") == ""


def test_domain_detects_clear_banking_reply() -> None:
    reply = "Your checking balance is $120 and your last transaction was a transfer."
    assert _detect_domain_from_text(reply, sent_message="Hi there") == "banking"


def test_domain_single_keyword_is_not_enough() -> None:
    assert _detect_domain_from_text("Let me book an appointment for your study session.") == ""
