"""Unit tests for chat_payload_extras slot-mode / nested-JSON payload support.

Covers TargetAppClient._build_generic_body: the flat-merge, no-extras, and
nested "slot mode" cases, plus token substitution ({{message}}/{{history}}/
{{session_id}}/{{conversation_id}}).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession


def _client(**kwargs) -> TargetAppClient:
    return TargetAppClient(base_url="http://localhost:9999", **kwargs)


def _session(**kwargs) -> AttackSession:
    return AttackSession(session_id="s1", target_url="http://localhost:9999", chain_id="c1", **kwargs)


class TestBuildGenericBodyNoExtras:
    def test_no_extras_flat_body_unchanged(self) -> None:
        client = _client()
        body = client._build_generic_body("hello", _session())
        assert body == {"message": "hello"}

    def test_no_extras_merges_session_context(self) -> None:
        client = _client()
        client._session_context = {"session_id": "abc"}
        body = client._build_generic_body("hello", _session())
        assert body == {"message": "hello", "session_id": "abc"}


class TestBuildGenericBodyFlatExtras:
    def test_flat_extras_merge_unchanged(self) -> None:
        """Today's behavior: flat extras merged as siblings, message key wins."""
        client = _client(chat_payload_extras={"vehicleState": "parked"})
        body = client._build_generic_body("hello", _session())
        assert body == {"vehicleState": "parked", "message": "hello"}

    def test_flat_extras_with_token_substitution(self) -> None:
        client = _client(chat_payload_extras={"user_id": "{{session_id}}"})
        client._session_context = {"session_id": "abc123"}
        body = client._build_generic_body("hello", _session())
        # session_context is still flat-merged in this mode (legacy behavior).
        assert body == {"user_id": "abc123", "message": "hello", "session_id": "abc123"}

    def test_flat_extras_without_tokens_untouched(self) -> None:
        client = _client(chat_payload_extras={"language": "en"})
        body = client._build_generic_body("hello", _session())
        assert body == {"language": "en", "message": "hello"}


class TestBuildGenericBodyNestedExtras:
    def test_nested_extras_becomes_full_body(self) -> None:
        """Nested chat_payload_extras (depth > 1) IS the body; chat_payload_key ignored.

        Regression guard: under the old flat-merge logic, {chat_payload_key: value}
        (default key "message") would clobber the nested "message" object below.
        """
        client = _client(
            chat_payload_extras={
                "message": {"role": "user", "content": "{{message}}"},
                "conversation_id": "{{conversation_id}}",
                "stream": False,
            },
        )
        client._session_context = {"conversation_id": "conv-1"}
        body = client._build_generic_body("attack payload", _session())
        assert body == {
            "message": {"role": "user", "content": "attack payload"},
            "conversation_id": "conv-1",
            "stream": False,
        }

    def test_nested_extras_drops_key_when_token_has_no_value_yet(self) -> None:
        """{{conversation_id}} isn't available on turn 1 — the key must be
        omitted entirely, not sent as a literal null."""
        client = _client(
            chat_payload_extras={
                "message": {"role": "user", "content": "{{message}}"},
                "conversation_id": "{{conversation_id}}",
                "stream": False,
            },
        )
        assert client._session_context == {}
        body = client._build_generic_body("first turn", _session())
        assert body == {
            "message": {"role": "user", "content": "first turn"},
            "stream": False,
        }
        assert "conversation_id" not in body

    def test_nested_extras_includes_key_once_token_resolves(self) -> None:
        """Same client/config as above, but after the server has echoed back
        a conversation_id on a prior turn — the key now appears."""
        client = _client(
            chat_payload_extras={
                "message": {"role": "user", "content": "{{message}}"},
                "conversation_id": "{{conversation_id}}",
                "stream": False,
            },
        )
        client._session_context = {"conversation_id": "conv-abc"}
        body = client._build_generic_body("second turn", _session())
        assert body == {
            "message": {"role": "user", "content": "second turn"},
            "conversation_id": "conv-abc",
            "stream": False,
        }

    def test_flat_extras_drops_key_when_token_has_no_value_yet(self) -> None:
        client = _client(chat_payload_extras={"user_id": "{{session_id}}"})
        body = client._build_generic_body("hello", _session())
        assert body == {"message": "hello"}
        assert "user_id" not in body

    def test_nested_extras_ignores_session_context_flat_merge(self) -> None:
        """Session context that isn't referenced by a token must not leak into a nested body."""
        client = _client(
            chat_payload_extras={"message": {"role": "user", "content": "{{message}}"}},
        )
        client._session_context = {"session_id": "sid-should-not-appear"}
        body = client._build_generic_body("hi", _session())
        assert body == {"message": {"role": "user", "content": "hi"}}

    def test_history_token_matches_message_history_builder(self) -> None:
        session = _session()
        session.add_turn("turn one", "reply one")
        client = _client(
            chat_payload_extras={"conversation": {"messages": "{{history}}"}, "stream": False},
        )
        body = client._build_generic_body("turn two", session)
        assert body == {
            "conversation": {
                "messages": [
                    {"role": "user", "content": "turn one"},
                    {"role": "assistant", "content": "reply one"},
                    {"role": "user", "content": "turn two"},
                ],
            },
            "stream": False,
        }

    def test_session_id_falls_back_to_thread_id(self) -> None:
        client = _client(
            chat_payload_extras={"message": {"content": "{{message}}"}, "session_id": "{{session_id}}"},
        )
        client._session_context = {"thread_id": "t1"}
        body = client._build_generic_body("hi", _session())
        assert body["session_id"] == "t1"

    def test_conversation_id_falls_back_to_chat_id(self) -> None:
        client = _client(
            chat_payload_extras={
                "message": {"content": "{{message}}"},
                "conversation_id": "{{conversation_id}}",
            },
        )
        client._session_context = {"chat_id": "c1"}
        body = client._build_generic_body("hi", _session())
        assert body["conversation_id"] == "c1"


@pytest.mark.asyncio
class TestSendUsesSharedBodyBuilder:
    async def test_send_posts_nested_body(self) -> None:
        """End-to-end: send() -> _send_impl posts the fully-substituted nested body."""
        client = _client(
            chat_payload_extras={
                "message": {"role": "user", "content": "{{message}}"},
                "stream": False,
            },
        )
        session = _session()

        resp_ok = MagicMock()
        resp_ok.raise_for_status = MagicMock()
        resp_ok.json = MagicMock(return_value={"response": "ok"})
        resp_ok.headers = {"content-type": "application/json"}

        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp_ok) as post_mock:
            text, _ = await client.send("attack payload", session)

        assert text == "ok"
        body = post_mock.await_args.kwargs["json"]
        assert body == {
            "message": {"role": "user", "content": "attack payload"},
            "stream": False,
        }
        await client.aclose()

    async def test_send_stream_uses_same_body_builder(self) -> None:
        """send_stream() builds its body via the same shared helper as send()."""
        client = _client(
            chat_payload_extras={"message": {"role": "user", "content": "{{message}}"}},
        )
        session = _session()

        expected_body = client._build_generic_body("attack payload", session)
        with patch.object(
            client, "_build_generic_body", wraps=client._build_generic_body
        ) as build_mock:
            with patch.object(client._client, "stream") as stream_mock:
                stream_mock.side_effect = RuntimeError("stop after body build")
                chunks = [chunk async for chunk in client.send_stream("attack payload", session)]

        assert chunks == [("[REQUEST_ERROR: RuntimeError: stop after body build]", [])]
        build_mock.assert_called_once_with("attack payload", session)
        assert stream_mock.call_args.args[1:] == ("/chat",)
        assert stream_mock.call_args.kwargs["json"] == expected_body
        await client.aclose()
