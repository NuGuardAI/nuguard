"""Tests for {{message}}-token payload templating in TargetAppClient.

Covers: token detection, template rendering, the new templated body path in
send()/send_impl() when chat_payload_extras defines {{message}}, and the two
regression guarantees — extras without {{message}} keep the existing merge
behaviour, and no extras at all keeps auto-detection untouched.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.redteam.target.client import (
    TargetAppClient,
    _contains_message_token,
    _render_payload_template,
)
from nuguard.redteam.target.session import AttackSession


def _mock_response(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_body)
    resp.status_code = status_code
    resp.text = json.dumps(json_body)
    resp.headers = {"content-type": "application/json"}
    return resp


class TestContainsMessageToken:
    def test_true_for_flat_string(self) -> None:
        assert _contains_message_token({"input": "{{message}}"})

    def test_true_nested_dict_and_list(self) -> None:
        assert _contains_message_token({"a": {"b": ["x", "{{message}} please"]}})

    def test_false_when_absent(self) -> None:
        assert not _contains_message_token({"user_id": "alice", "lang": "en"})

    def test_false_for_empty_or_non_string_values(self) -> None:
        assert not _contains_message_token({"count": 3, "flag": True, "nested": {}})


class TestRenderPayloadTemplate:
    def test_substitutes_all_known_tokens(self) -> None:
        template = {
            "input": {"text": "{{message}}", "history": "{{history}}"},
            "session_id": "{{session_id}}",
            "conversation_id": "{{conversation_id}}",
        }
        rendered = _render_payload_template(
            template,
            message="hello",
            history="[]",
            session_id="s-1",
            conversation_id="c-1",
        )
        assert rendered == {
            "input": {"text": "hello", "history": "[]"},
            "session_id": "s-1",
            "conversation_id": "c-1",
        }

    def test_leaves_unknown_tokens_untouched(self) -> None:
        rendered = _render_payload_template(
            {"a": "{{message}} {{unknown_token}}"},
            message="hi",
            history="",
            session_id="",
            conversation_id="",
        )
        assert rendered == {"a": "hi {{unknown_token}}"}

    def test_does_not_mutate_original_template(self) -> None:
        template = {"a": "{{message}}"}
        _render_payload_template(template, message="hi", history="", session_id="", conversation_id="")
        assert template == {"a": "{{message}}"}

    def test_multiple_tokens_in_one_string(self) -> None:
        rendered = _render_payload_template(
            {"a": "msg={{message}} sid={{session_id}}"},
            message="hi",
            history="",
            session_id="s1",
            conversation_id="",
        )
        assert rendered == {"a": "msg=hi sid=s1"}


@pytest.mark.asyncio
async def test_send_uses_templated_body_when_message_token_present() -> None:
    client = TargetAppClient(
        base_url="http://localhost:9999",
        chat_payload_extras={"input": {"text": "{{message}}"}, "lang": "en"},
    )
    session = AttackSession(session_id="s1", target_url="http://localhost:9999", chain_id="c1")

    resp = _mock_response({"response": "ok"})
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp) as post_mock:
        text, _ = await client.send("hello there", session)

    assert text == "ok"
    sent_body = post_mock.await_args.kwargs["json"]
    assert sent_body == {"input": {"text": "hello there"}, "lang": "en"}
    await client.aclose()


@pytest.mark.asyncio
async def test_send_templated_body_threads_history_session_and_conversation() -> None:
    client = TargetAppClient(
        base_url="http://localhost:9999",
        chat_payload_extras={
            "input": {"text": "{{message}}", "history": "{{history}}"},
            "session_id": "{{session_id}}",
            "conversation_id": "{{conversation_id}}",
        },
    )
    session = AttackSession(session_id="s1", target_url="http://localhost:9999", chain_id="c1")
    session.add_turn("first prompt", "first response")

    # Simulate a conversation_id previously echoed back by the target.
    client._session_context["conversation_id"] = "conv-42"

    resp = _mock_response({"response": "second reply"})
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp) as post_mock:
        text, _ = await client.send("second prompt", session)

    assert text == "second reply"
    sent_body = post_mock.await_args.kwargs["json"]
    assert sent_body["input"]["text"] == "second prompt"
    history = json.loads(sent_body["input"]["history"])
    assert history == [{"role": "user", "content": "first prompt"}, {"role": "assistant", "content": "first response"}]
    assert sent_body["session_id"] == "s1"
    assert sent_body["conversation_id"] == "conv-42"
    await client.aclose()


@pytest.mark.asyncio
async def test_send_extras_without_message_token_keeps_existing_merge_behavior() -> None:
    """Regression: chat_payload_extras without {{message}} merges exactly as before."""
    client = TargetAppClient(
        base_url="http://localhost:9999",
        chat_payload_key="text",
        chat_payload_extras={"vehicleState": "parked", "language": "en"},
    )
    session = AttackSession(session_id="s1", target_url="http://localhost:9999", chain_id="c1")

    resp = _mock_response({"response": "ok"})
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp) as post_mock:
        text, _ = await client.send("hello", session)

    assert text == "ok"
    sent_body = post_mock.await_args.kwargs["json"]
    assert sent_body == {"vehicleState": "parked", "language": "en", "text": "hello"}
    await client.aclose()


@pytest.mark.asyncio
async def test_send_without_extras_keeps_auto_detection_untouched() -> None:
    """Regression: no chat_payload_extras at all — auto-detected flat body, unchanged."""
    client = TargetAppClient(base_url="http://localhost:9999", chat_payload_key="message")
    session = AttackSession(session_id="s1", target_url="http://localhost:9999", chain_id="c1")

    resp = _mock_response({"response": "ok"})
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp) as post_mock:
        text, _ = await client.send("hello", session)

    assert text == "ok"
    sent_body = post_mock.await_args.kwargs["json"]
    assert sent_body == {"message": "hello"}
    await client.aclose()


@pytest.mark.asyncio
async def test_debug_logs_request_and_response_bodies() -> None:
    """Debug logging fires with the exact outgoing/incoming JSON bodies.

    Patches the module logger directly rather than relying on caplog, since
    get_logger() configures a non-propagating logger (propagate=False) whose
    effective level is fixed by NUGUARD_LOG_LEVEL at first use.
    """
    client = TargetAppClient(
        base_url="http://localhost:9999",
        chat_payload_extras={"input": {"text": "{{message}}"}},
    )
    session = AttackSession(session_id="s1", target_url="http://localhost:9999", chain_id="c1")

    resp = _mock_response({"response": "debug-ok"})
    with patch("nuguard.redteam.target.client._log") as log_mock:
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp):
            await client.send("hello debug", session)

    debug_calls = [call.args for call in log_mock.debug.call_args_list]
    assert any(
        "Target HTTP POST" in args[0] and "hello debug" in args[2] for args in debug_calls
    )
    assert any(
        "Target HTTP Response" in args[0] and "debug-ok" in args[2] for args in debug_calls
    )
    await client.aclose()
