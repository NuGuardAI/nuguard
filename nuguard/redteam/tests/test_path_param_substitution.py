"""Tests for TargetAppClient's :name/{name} path-param substitution.

Part of the two-step (resource-bootstrap) chat flow — see
tests/apps/studyield-app/2-step-chat.md. A chat path like NestJS's
``/chat/conversations/:id/messages`` can't be requested literally; the
``:id`` segment must be resolved (via set_path_param()) from a prerequisite
resource-creation call before any turn is sent.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from nuguard.redteam.target.client import TargetAppClient, _substitute_path_params
from nuguard.redteam.target.session import AttackSession

BASE = "http://test-app"


def _session() -> AttackSession:
    return AttackSession(session_id="s1", target_url=BASE, chain_id="c1")


class TestSubstitutePathParamsHelper:
    def test_colon_style_substituted(self) -> None:
        resolved, missing = _substitute_path_params(
            "/chat/conversations/:id/messages", {"id": "c_123"}
        )
        assert resolved == "/chat/conversations/c_123/messages"
        assert missing == []

    def test_brace_style_substituted(self) -> None:
        resolved, missing = _substitute_path_params("/user/{id}/chat", {"id": "u_1"})
        assert resolved == "/user/u_1/chat"
        assert missing == []

    def test_missing_value_reported_and_left_untouched(self) -> None:
        resolved, missing = _substitute_path_params(
            "/chat/conversations/:id/messages", {}
        )
        assert resolved == "/chat/conversations/:id/messages"
        assert missing == ["id"]

    def test_multiple_params(self) -> None:
        resolved, missing = _substitute_path_params(
            "/orgs/:orgId/projects/:projectId/chat",
            {"orgId": "o1", "projectId": "p1"},
        )
        assert resolved == "/orgs/o1/projects/p1/chat"
        assert missing == []

    def test_path_without_params_unaffected(self) -> None:
        resolved, missing = _substitute_path_params("/chat", {"id": "unused"})
        assert resolved == "/chat"
        assert missing == []


class TestSendSubstitutesBoundPathParam:
    @pytest.mark.asyncio
    @respx.mock
    async def test_bound_param_is_substituted_into_request_url(self) -> None:
        route = respx.post(f"{BASE}/chat/conversations/c_123/messages").mock(
            return_value=httpx.Response(200, json={"response": "hi"})
        )
        client = TargetAppClient(
            base_url=BASE,
            chat_path="/chat/conversations/:id/messages",
            chat_payload_key="content",
        )
        client.set_path_param("id", "c_123")
        async with client:
            text, _ = await client.send("hello", _session())
        assert route.called
        assert text == "hi"

    @pytest.mark.asyncio
    async def test_unresolved_param_returns_config_error_without_request(self) -> None:
        client = TargetAppClient(
            base_url=BASE,
            chat_path="/chat/conversations/:id/messages",
            chat_payload_key="content",
        )
        async with client:
            text, calls = await client.send("hello", _session())
        assert text == "[CONFIG_ERROR: unresolved path param 'id']"
        assert calls == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_set_chat_endpoint_clears_stale_param_binding(self) -> None:
        client = TargetAppClient(
            base_url=BASE,
            chat_path="/chat/conversations/:id/messages",
            chat_payload_key="content",
        )
        client.set_path_param("id", "stale_value")
        client.set_chat_endpoint("/other/:id/chat", "content", False)

        text, _ = await client.send("hello", _session())
        assert text == "[CONFIG_ERROR: unresolved path param 'id']"
