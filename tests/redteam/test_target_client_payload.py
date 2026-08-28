"""Tests for TargetAppClient's chat_payload_extras body construction.

Covers the flat-merge (legacy) path, the nested slot-mode path (token
substitution), and the interaction between the two and multi-turn session
state (history replay, session/conversation id propagation).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession


def _make_session(session_id: str = "s1") -> AttackSession:
    return AttackSession(session_id=session_id, target_url="http://localhost:9999", chain_id="c1")


class TestBuildGenericBodyUnset:
    def test_no_extras_unchanged_behavior(self) -> None:
        client = TargetAppClient(base_url="http://localhost:9999", chat_payload_key="message")
        session = _make_session()
        body = client._build_generic_body("hello", session)
        assert body == {"message": "hello"}

    def test_session_context_merged_when_extras_unset(self) -> None:
        client = TargetAppClient(base_url="http://localhost:9999", chat_payload_key="message")
        session = _make_session()
        client._session_context = {"session_id": "abc"}
        body = client._build_generic_body("hello", session)
        assert body == {"session_id": "abc", "message": "hello"}


class TestBuildGenericBodyFlat:
    def test_flat_extras_merged_as_siblings(self) -> None:
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_key="message",
            chat_payload_extras={"user_id": "alice", "language": "en"},
        )
        session = _make_session()
        body = client._build_generic_body("hello", session)
        assert body == {"user_id": "alice", "language": "en", "message": "hello"}

    def test_message_key_wins_on_collision(self) -> None:
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_key="message",
            chat_payload_extras={"message": "should be overridden"},
        )
        session = _make_session()
        body = client._build_generic_body("hello", session)
        assert body == {"message": "hello"}

    def test_token_inside_flat_extras_value_is_substituted(self) -> None:
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_key="message",
            chat_payload_extras={"user_id": "{{session_id}}"},
        )
        session = _make_session()
        client._session_context = {"session_id": "srv-123"}
        body = client._build_generic_body("hello", session)
        # session_context is also unconditionally merged in the flat path
        # (legacy behavior, unchanged), independent of the extras substitution.
        assert body == {"user_id": "srv-123", "session_id": "srv-123", "message": "hello"}

    def test_flat_extras_token_resolving_to_none_drops_key(self) -> None:
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_key="message",
            chat_payload_extras={"conversation_id": "{{conversation_id}}", "language": "en"},
        )
        session = _make_session()
        body = client._build_generic_body("hello", session)
        assert body == {"language": "en", "message": "hello"}


class TestBuildGenericBodyNested:
    def test_nested_extras_becomes_entire_body_ignoring_payload_key(self) -> None:
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_key="message",
            chat_payload_extras={
                "message": {"role": "user", "content": "{{message}}"},
                "conversation_id": "{{conversation_id}}",
                "stream": False,
            },
        )
        session = _make_session()
        body = client._build_generic_body("hello", session)
        # conversation_id is dropped (unresolved on turn 1); chat_payload_key
        # is ignored entirely — the substituted extras structure IS the body.
        assert body == {
            "message": {"role": "user", "content": "hello"},
            "stream": False,
        }

    def test_nested_extras_conversation_id_present_after_echoed(self) -> None:
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_extras={
                "message": {"role": "user", "content": "{{message}}"},
                "conversation_id": "{{conversation_id}}",
            },
        )
        session = _make_session()
        client._session_context = {"conversation_id": "conv-42"}
        body = client._build_generic_body("hello turn 2", session)
        assert body == {
            "message": {"role": "user", "content": "hello turn 2"},
            "conversation_id": "conv-42",
        }

    def test_history_token_matches_session_turns_replay(self) -> None:
        # Nested — the history token lives under a wrapper key, forcing
        # slot-mode (depth > 1) rather than the flat sibling-merge path.
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_extras={"payload": {"history": "{{history}}"}},
        )
        session = _make_session()
        session.add_turn("first prompt", "first response")
        body = client._build_generic_body("second prompt", session)
        assert body == {
            "payload": {
                "history": [
                    {"role": "user", "content": "first prompt"},
                    {"role": "assistant", "content": "first response"},
                    {"role": "user", "content": "second prompt"},
                ]
            }
        }

    def test_session_id_falls_back_to_thread_id_then_chat_id(self) -> None:
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_extras={"context": {"session_id": "{{session_id}}"}},
        )
        session = _make_session()
        client._session_context = {"thread_id": "t-1"}
        body = client._build_generic_body("hello", session)
        assert body == {"context": {"session_id": "t-1"}}

    def test_flat_chat_payload_key_ignored_when_nested_extras_used(self) -> None:
        """A blind merge would clobber the nested "message" object; verify it doesn't."""
        client = TargetAppClient(
            base_url="http://localhost:9999",
            chat_payload_key="message",
            chat_payload_extras={"message": {"role": "user", "content": "{{message}}"}},
        )
        session = _make_session()
        body = client._build_generic_body("hi", session)
        assert body["message"] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_send_uses_nested_slot_mode_body_end_to_end() -> None:
    """_send_impl posts the fully-substituted nested body, not a flat merge."""
    client = TargetAppClient(
        base_url="http://localhost:9999",
        chat_payload_extras={
            "message": {"role": "user", "content": "{{message}}"},
            "conversation_id": "{{conversation_id}}",
            "stream": False,
        },
        chat_response_key="content",
    )
    session = _make_session()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json = MagicMock(return_value={"content": "hi back", "conversation_id": "conv-9"})

    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as post_mock:
        text, _ = await client.send("hello", session)

    assert text == "hi back"
    sent_body = post_mock.call_args.kwargs["json"]
    assert sent_body == {
        "message": {"role": "user", "content": "hello"},
        "stream": False,
    }
    # conversation_id from the response is now tracked for the next turn.
    assert client._session_context["conversation_id"] == "conv-9"

    await client.aclose()
