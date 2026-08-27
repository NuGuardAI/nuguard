"""Tests for TargetAppClient's OpenAI-style messages=[{role,content}] payload shaping.

Covers the fix for endpoints like Phlox's ``/api/chat``, which only accepts
``{"messages": [{"role": "user", "content": "..."}], ...}`` — before this fix,
TargetAppClient always sent either a flat string or a bare-string list, so any
such endpoint rejected every request with 400/422 and every scenario aborted
after 3 consecutive failures.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession

BASE = "http://test-app"
CHAT = "/chat"


def _session() -> AttackSession:
    return AttackSession(session_id="s1", target_url=BASE, chain_id="c1")


@pytest.mark.asyncio
@respx.mock
async def test_messages_key_sends_role_content_list_on_first_turn():
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "hi there"})
    )
    client = TargetAppClient(
        base_url=BASE, chat_path=CHAT, chat_payload_key="messages", chat_payload_list=True,
    )
    async with client:
        await client.send("hello", _session())
    body = json.loads(route.calls.last.request.content)
    assert body == {"messages": [{"role": "user", "content": "hello"}]}


@pytest.mark.asyncio
@respx.mock
async def test_messages_key_replays_prior_turns_as_history():
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "turn 2 reply"})
    )
    client = TargetAppClient(
        base_url=BASE, chat_path=CHAT, chat_payload_key="messages", chat_payload_list=True,
    )
    session = _session()
    session.add_turn(prompt="first message", response="first reply")
    async with client:
        await client.send("second message", session)
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "messages": [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "second message"},
        ]
    }


@pytest.mark.asyncio
@respx.mock
async def test_history_alias_key_also_shapes_as_messages():
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    client = TargetAppClient(
        base_url=BASE, chat_path=CHAT, chat_payload_key="history", chat_payload_list=True,
    )
    async with client:
        await client.send("hi", _session())
    body = json.loads(route.calls.last.request.content)
    assert body == {"history": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
@respx.mock
async def test_non_message_history_list_key_unaffected():
    """Regression guard: a list-shaped key that isn't a known history name
    (e.g. LangGraph's 'phrases') keeps wrapping the bare string, unchanged."""
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    client = TargetAppClient(
        base_url=BASE, chat_path=CHAT, chat_payload_key="phrases", chat_payload_list=True,
    )
    async with client:
        await client.send("hi", _session())
    body = json.loads(route.calls.last.request.content)
    assert body == {"phrases": ["hi"]}


@pytest.mark.asyncio
@respx.mock
async def test_flat_key_unaffected_by_message_history_change():
    """Regression guard: chat_payload_list=False keeps sending the raw string,
    even if the key happens to be named 'messages'."""
    route = respx.post(f"{BASE}{CHAT}").mock(
        return_value=httpx.Response(200, json={"response": "ok"})
    )
    client = TargetAppClient(
        base_url=BASE, chat_path=CHAT, chat_payload_key="messages", chat_payload_list=False,
    )
    async with client:
        await client.send("hi", _session())
    body = json.loads(route.calls.last.request.content)
    assert body == {"messages": "hi"}
