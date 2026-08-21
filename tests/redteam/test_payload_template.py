"""Tests for generic REST request-body shaping via ``chat_payload_template``.

Covers:
- :func:`~nuguard.redteam.target.client._substitute_placeholders`
- :func:`~nuguard.redteam.target.client._build_generic_body`
- :func:`~nuguard.redteam.target.client.history_from_session`
- End-to-end :meth:`~nuguard.redteam.target.client.TargetAppClient.send` /
  ``send_stream`` body construction and nested SSE extraction
"""
from __future__ import annotations

import copy
import json

import httpx
import pytest
import respx

from nuguard.redteam.target.client import (
    HTTP_BODY_LOG_ENV,
    TargetAppClient,
    _build_generic_body,
    _substitute_placeholders,
    history_from_session,
    http_body_logging_enabled,
)
from nuguard.redteam.target.session import AttackSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session(*turns: tuple[str, str]) -> AttackSession:
    s = AttackSession(session_id="s", target_url="http://app.test", chain_id="c")
    for prompt, response in turns:
        s.add_turn(prompt, response)
    return s


def _flat(payload: str, session: AttackSession, **kw):
    defaults: dict = {
        "payload_key": "message",
        "payload_list": False,
        "payload_extras": None,
        "payload_template": None,
        "session_context": None,
    }
    defaults.update(kw)
    return _build_generic_body(payload, session, **defaults)


def _sub(node, **kw):
    defaults: dict = {
        "message": "MSG",
        "history": [],
        "session_id": None,
        "conversation_id": None,
        "extras": {},
    }
    defaults.update(kw)
    return _substitute_placeholders(node, **defaults)


# ---------------------------------------------------------------------------
# history_from_session
# ---------------------------------------------------------------------------

class TestHistoryFromSession:
    def test_empty_session_yields_empty_history(self) -> None:
        assert history_from_session(_session()) == []

    def test_none_session_yields_empty_history(self) -> None:
        assert history_from_session(None) == []

    def test_turns_render_oldest_first_as_role_content(self) -> None:
        history = history_from_session(_session(("q1", "a1"), ("q2", "a2")))
        assert history == [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]

    def test_empty_response_is_skipped(self) -> None:
        # An empty assistant content field is rejected by some APIs.
        assert history_from_session(_session(("q1", ""))) == [
            {"role": "user", "content": "q1"}
        ]


# ---------------------------------------------------------------------------
# _substitute_placeholders
# ---------------------------------------------------------------------------

class TestSubstitution:
    def test_message_token_replaced_in_nested_object(self) -> None:
        template = {"message": {"role": "user", "content": "{{message}}"}}
        assert _sub(template) == {"message": {"role": "user", "content": "MSG"}}

    def test_message_token_only_matches_whole_string(self) -> None:
        # Substring interpolation is deliberately unsupported.
        assert _sub({"a": "prefix {{message}}"}) == {"a": "prefix {{message}}"}

    def test_history_splices_flat_as_list_element(self) -> None:
        history = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        template = {
            "messages": [
                {"role": "system", "content": "sys"},
                "{{history}}",
                {"role": "user", "content": "{{message}}"},
            ]
        }
        assert _sub(template, history=history) == {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "MSG"},
            ]
        }

    def test_history_nests_as_list_when_used_as_dict_value(self) -> None:
        history = [{"role": "user", "content": "q1"}]
        assert _sub({"prior": "{{history}}"}, history=history) == {
            "prior": [{"role": "user", "content": "q1"}]
        }

    def test_empty_history_splices_to_nothing(self) -> None:
        template = {"messages": ["{{history}}", {"content": "{{message}}"}]}
        assert _sub(template) == {"messages": [{"content": "MSG"}]}

    def test_session_and_conversation_ids_substituted(self) -> None:
        template = {"sid": "{{session_id}}", "cid": "{{conversation_id}}"}
        out = _sub(template, session_id="s-1", conversation_id="c-1")
        assert out == {"sid": "s-1", "cid": "c-1"}

    # ── D1: unresolved session placeholders ────────────────────────────────
    def test_unresolved_session_key_is_dropped_not_nulled(self) -> None:
        out = _sub({"m": "{{message}}", "sid": "{{session_id}}"})
        assert out == {"m": "MSG"}
        assert "sid" not in out

    def test_unresolved_placeholder_prunes_emptied_parent(self) -> None:
        template = {"conversation": {"id": "{{conversation_id}}"}, "m": "{{message}}"}
        assert _sub(template) == {"m": "MSG"}

    def test_pruning_cascades_through_two_levels(self) -> None:
        template = {"a": {"b": {"id": "{{session_id}}"}}, "m": "{{message}}"}
        assert _sub(template) == {"m": "MSG"}

    def test_literal_empty_container_is_preserved(self) -> None:
        # Operator wrote {} deliberately — not the result of pruning.
        assert _sub({"opts": {}, "m": "{{message}}"}) == {"opts": {}, "m": "MSG"}

    def test_unresolved_placeholder_dropped_from_list(self) -> None:
        assert _sub({"ids": ["{{session_id}}", "keep"]}) == {"ids": ["keep"]}

    # ── D3: extras splice ──────────────────────────────────────────────────
    def test_extras_splice_into_containing_object(self) -> None:
        template = {"meta": {"{{extras}}": {}}, "m": "{{message}}"}
        out = _sub(template, extras={"user_id": "alice", "tenant": "acme"})
        assert out == {"meta": {"user_id": "alice", "tenant": "acme"}, "m": "MSG"}

    def test_extras_splice_at_top_level_preserves_marker_position(self) -> None:
        template = {"model": "gpt-4o", "{{extras}}": {}, "m": "{{message}}"}
        out = _sub(template, extras={"user_id": "alice"})
        assert list(out) == ["model", "user_id", "m"]

    @pytest.mark.parametrize("marker_value", [{}, None, "", "ignored"])
    def test_extras_marker_value_is_ignored(self, marker_value) -> None:
        template = {"{{extras}}": marker_value, "m": "{{message}}"}
        out = _sub(template, extras={"user_id": "alice"})
        assert out == {"user_id": "alice", "m": "MSG"}

    def test_empty_extras_splices_to_nothing(self) -> None:
        assert _sub({"{{extras}}": {}, "m": "{{message}}"}, extras={}) == {"m": "MSG"}

    def test_literal_template_key_wins_collision_with_extras(self) -> None:
        template = {"model": "gpt-4o", "{{extras}}": {}, "m": "{{message}}"}
        out = _sub(template, extras={"model": "from-extras", "user_id": "alice"})
        assert out["model"] == "gpt-4o"
        assert out["user_id"] == "alice"

    def test_literal_key_wins_even_when_declared_after_marker(self) -> None:
        template = {"{{extras}}": {}, "model": "gpt-4o", "m": "{{message}}"}
        out = _sub(template, extras={"model": "from-extras"})
        assert out["model"] == "gpt-4o"

    def test_spliced_extras_values_are_deep_copied(self) -> None:
        extras = {"ctx": {"nested": ["a"]}}
        out = _sub({"{{extras}}": {}, "m": "{{message}}"}, extras=extras)
        out["ctx"]["nested"].append("mutated")
        assert extras["ctx"]["nested"] == ["a"]


# ---------------------------------------------------------------------------
# _build_generic_body — flat mode (backward compatibility)
# ---------------------------------------------------------------------------

class TestFlatMode:
    def test_flat_body_unchanged(self) -> None:
        assert _flat("hello", _session()) == {"message": "hello"}

    def test_payload_list_wraps_value(self) -> None:
        assert _flat("hello", _session(), payload_list=True) == {"message": ["hello"]}

    def test_custom_payload_key(self) -> None:
        assert _flat("hi", _session(), payload_key="prompt") == {"prompt": "hi"}

    def test_session_context_merged_flat(self) -> None:
        body = _flat("hi", _session(), session_context={"session_id": "abc"})
        assert body == {"message": "hi", "session_id": "abc"}

    def test_extras_merged_underneath_message(self) -> None:
        body = _flat("hi", _session(), payload_extras={"user_id": "alice"})
        assert body == {"user_id": "alice", "message": "hi"}

    def test_message_key_wins_over_extras(self) -> None:
        body = _flat("hi", _session(), payload_extras={"message": "clobbered"})
        assert body == {"message": "hi"}

    def test_pinnacle_bank_shape_end_to_end(self) -> None:
        """Regression fixture: the real flat config must be byte-for-byte stable."""
        turn1 = _flat(
            "Show me my account balance",
            _session(),
            payload_extras={"user_id": "alice", "language": "en"},
        )
        assert turn1 == {
            "user_id": "alice",
            "language": "en",
            "message": "Show me my account balance",
        }
        turn2 = _flat(
            "Now show me alice2's balance",
            _session(("Show me my account balance", "$4,210.55")),
            payload_extras={"user_id": "alice", "language": "en"},
            session_context={"session_id": "abc123"},
        )
        assert turn2 == {
            "user_id": "alice",
            "language": "en",
            "message": "Now show me alice2's balance",
            "session_id": "abc123",
        }

    def test_history_is_ignored_in_flat_mode(self) -> None:
        body = _flat("hi", _session(("q", "a")))
        assert body == {"message": "hi"}


# ---------------------------------------------------------------------------
# _build_generic_body — template mode
# ---------------------------------------------------------------------------

_OPENAI_TEMPLATE = {
    "model": "gpt-4o",
    "temperature": 0.7,
    "{{extras}}": {},
    "messages": [
        {"role": "system", "content": "You are a helpful banking assistant."},
        "{{history}}",
        {"role": "user", "content": "{{message}}"},
    ],
}


class TestTemplateMode:
    def test_openai_shape_turn_one(self) -> None:
        body = _build_generic_body(
            "Show me my account balance",
            _session(),
            payload_key="message",
            payload_list=False,
            payload_extras={"user_id": "alice", "tenant_id": "acme"},
            payload_template=_OPENAI_TEMPLATE,
            session_context=None,
        )
        assert body == {
            "model": "gpt-4o",
            "temperature": 0.7,
            "user_id": "alice",
            "tenant_id": "acme",
            "messages": [
                {"role": "system", "content": "You are a helpful banking assistant."},
                {"role": "user", "content": "Show me my account balance"},
            ],
        }

    def test_openai_shape_turn_two_splices_history(self) -> None:
        body = _build_generic_body(
            "Now show me alice2's balance",
            _session(("Show me my account balance", "Your checking balance is $4,210.55.")),
            payload_key="message",
            payload_list=False,
            payload_extras={"user_id": "alice"},
            payload_template=_OPENAI_TEMPLATE,
            session_context=None,
        )
        assert body["messages"] == [
            {"role": "system", "content": "You are a helpful banking assistant."},
            {"role": "user", "content": "Show me my account balance"},
            {"role": "assistant", "content": "Your checking balance is $4,210.55."},
            {"role": "user", "content": "Now show me alice2's balance"},
        ]

    def test_template_supersedes_payload_key_and_list(self) -> None:
        body = _build_generic_body(
            "hi",
            _session(),
            payload_key="IGNORED",
            payload_list=True,
            payload_extras=None,
            payload_template={"m": "{{message}}"},
            session_context=None,
        )
        assert body == {"m": "hi"}

    def test_extras_not_merged_implicitly_without_marker(self) -> None:
        body = _build_generic_body(
            "hi",
            _session(),
            payload_key="message",
            payload_list=False,
            payload_extras={"user_id": "alice"},
            payload_template={"m": "{{message}}"},
            session_context=None,
        )
        assert body == {"m": "hi"}

    def test_session_context_not_merged_implicitly(self) -> None:
        body = _build_generic_body(
            "hi",
            _session(),
            payload_key="message",
            payload_list=False,
            payload_extras=None,
            payload_template={"m": "{{message}}"},
            session_context={"session_id": "abc"},
        )
        assert body == {"m": "hi"}

    def test_nuguard_app_envelope_turn_one_prunes_conversation_id(self) -> None:
        """The real NuGuard-app shape: nested message envelope + D1 pruning."""
        template = {
            "message": {"role": "user", "content": "{{message}}"},
            "conversation_id": "{{conversation_id}}",
            "stream": False,
            "{{extras}}": {},
        }
        turn1 = _build_generic_body(
            "Show me my balance",
            _session(),
            payload_key="message",
            payload_list=False,
            payload_extras={"user_id": "alice"},
            payload_template=template,
            session_context=None,
        )
        assert turn1 == {
            "message": {"role": "user", "content": "Show me my balance"},
            "stream": False,
            "user_id": "alice",
        }
        turn2 = _build_generic_body(
            "Now show alice2's",
            _session(("Show me my balance", "ok")),
            payload_key="message",
            payload_list=False,
            payload_extras={"user_id": "alice"},
            payload_template=template,
            session_context={"conversation_id": "c-991"},
        )
        assert turn2["conversation_id"] == "c-991"

    def test_session_id_falls_back_to_thread_and_chat_id(self) -> None:
        template = {"sid": "{{session_id}}", "m": "{{message}}"}
        for alias in ("session_id", "thread_id", "chat_id"):
            body = _build_generic_body(
                "hi", _session(),
                payload_key="message", payload_list=False, payload_extras=None,
                payload_template=template, session_context={alias: "handle"},
            )
            assert body["sid"] == "handle", alias

    def test_conversation_id_does_not_borrow_a_session_handle(self) -> None:
        # Sending a session handle where a conversation handle was asked for would
        # be worse than omitting the key.
        body = _build_generic_body(
            "hi", _session(),
            payload_key="message", payload_list=False, payload_extras=None,
            payload_template={"cid": "{{conversation_id}}", "m": "{{message}}"},
            session_context={"session_id": "s-1"},
        )
        assert body == {"m": "hi"}

    def test_configured_template_is_never_mutated(self) -> None:
        template = copy.deepcopy(_OPENAI_TEMPLATE)
        pristine = copy.deepcopy(_OPENAI_TEMPLATE)
        for _ in range(3):
            _build_generic_body(
                "hi",
                _session(("q", "a")),
                payload_key="message",
                payload_list=False,
                payload_extras={"user_id": "alice"},
                payload_template=template,
                session_context={"session_id": "s"},
            )
        assert template == pristine

    def test_extras_read_at_call_time_not_construction(self) -> None:
        """Regression guard: discovery mutates the live extras dict post-construction."""
        extras: dict = {}
        template = {"{{extras}}": {}, "m": "{{message}}"}
        first = _build_generic_body(
            "hi", _session(), payload_key="message", payload_list=False,
            payload_extras=extras, payload_template=template, session_context=None,
        )
        assert first == {"m": "hi"}
        extras["user_id"] = "alice"  # e.g. injected from a login response
        second = _build_generic_body(
            "hi", _session(), payload_key="message", payload_list=False,
            payload_extras=extras, payload_template=template, session_context=None,
        )
        assert second == {"user_id": "alice", "m": "hi"}


# ---------------------------------------------------------------------------
# End-to-end TargetAppClient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestClientSendWithTemplate:
    async def test_send_posts_exact_nested_body(self) -> None:
        captured: dict = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "hi there"}}]}
            )

        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/v1/chat/completions").mock(side_effect=_capture)
            client = TargetAppClient(
                base_url="http://app.test",
                chat_path="/v1/chat/completions",
                chat_response_key="choices[0].message.content",
                chat_payload_extras={"user_id": "alice"},
                chat_payload_template=_OPENAI_TEMPLATE,
            )
            async with client:
                text, _ = await client.send("attack payload", _session())

        assert text == "hi there"
        assert captured == {
            "model": "gpt-4o",
            "temperature": 0.7,
            "user_id": "alice",
            "messages": [
                {"role": "system", "content": "You are a helpful banking assistant."},
                {"role": "user", "content": "attack payload"},
            ],
        }

    async def test_send_without_template_keeps_flat_body(self) -> None:
        captured: dict = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"response": "ok"})

        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/chat").mock(side_effect=_capture)
            client = TargetAppClient(
                base_url="http://app.test",
                chat_response_key="response",
                chat_payload_extras={"user_id": "alice"},
            )
            async with client:
                await client.send("hello", _session())

        assert captured == {"user_id": "alice", "message": "hello"}

    async def test_second_turn_carries_conversation_id_and_history(self) -> None:
        bodies: list[dict] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"content": "ok", "conversation_id": "c-991"})

        template = {
            "message": {"role": "user", "content": "{{message}}"},
            "conversation_id": "{{conversation_id}}",
            "history": "{{history}}",
        }
        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/chat").mock(side_effect=_capture)
            client = TargetAppClient(
                base_url="http://app.test",
                chat_response_key="content",
                chat_payload_template=template,
            )
            session = _session()
            async with client:
                text1, _ = await client.send("first", session)
                session.add_turn("first", text1)
                await client.send("second", session)

        # Turn 1: no conversation_id yet → key pruned; history empty.
        assert "conversation_id" not in bodies[0]
        assert bodies[0]["history"] == []
        # Turn 2: server-issued id echoed back, prior turn present.
        assert bodies[1]["conversation_id"] == "c-991"
        assert bodies[1]["history"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
        ]


@pytest.mark.asyncio
class TestSendStreamExtraction:
    async def _collect(self, client: TargetAppClient) -> list[str]:
        return [chunk async for chunk, _ in client.send_stream("hi", _session())]

    async def test_sse_uses_dotted_response_key(self) -> None:
        sse = (
            b'data: {"delta": {"text": "Hello "}}\n\n'
            b'data: {"delta": {"text": "world"}}\n\n'
        )
        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/chat").mock(
                return_value=httpx.Response(
                    200, content=sse, headers={"content-type": "text/event-stream"}
                )
            )
            client = TargetAppClient(
                base_url="http://app.test", chat_response_key="delta.text"
            )
            async with client:
                chunks = await self._collect(client)

        assert chunks == ["Hello ", "world"]

    async def test_sse_nested_message_object_yields_str_not_dict(self) -> None:
        """A {"message": {...}} event must not leak a dict out of the generator."""
        sse = b'data: {"message": {"content": "nested"}}\n\n'
        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/chat").mock(
                return_value=httpx.Response(
                    200, content=sse, headers={"content-type": "text/event-stream"}
                )
            )
            client = TargetAppClient(base_url="http://app.test")
            async with client:
                chunks = await self._collect(client)

        assert all(isinstance(c, str) for c in chunks)

    async def test_sse_flat_fallback_still_works(self) -> None:
        sse = b'data: {"text": "flat"}\n\n'
        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/chat").mock(
                return_value=httpx.Response(
                    200, content=sse, headers={"content-type": "text/event-stream"}
                )
            )
            client = TargetAppClient(base_url="http://app.test")
            async with client:
                chunks = await self._collect(client)

        assert chunks == ["flat"]

    async def test_stream_uses_template_for_body(self) -> None:
        captured: dict = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                content=b'data: {"text": "ok"}\n\n',
                headers={"content-type": "text/event-stream"},
            )

        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/chat").mock(side_effect=_capture)
            client = TargetAppClient(
                base_url="http://app.test",
                chat_payload_template={"message": {"content": "{{message}}"}},
            )
            async with client:
                await self._collect(client)

        assert captured == {"message": {"content": "hi"}}


# ---------------------------------------------------------------------------
# Opt-in HTTP body tracing
# ---------------------------------------------------------------------------

class TestHttpBodyLoggingToggle:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
    def test_enabled_values(self, monkeypatch, value) -> None:
        monkeypatch.setenv(HTTP_BODY_LOG_ENV, value)
        assert http_body_logging_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_disabled_values(self, monkeypatch, value) -> None:
        monkeypatch.setenv(HTTP_BODY_LOG_ENV, value)
        assert http_body_logging_enabled() is False

    def test_disabled_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv(HTTP_BODY_LOG_ENV, raising=False)
        assert http_body_logging_enabled() is False


@pytest.mark.asyncio
class TestHttpBodyTraceOutput:
    async def _send(self, monkeypatch, enabled: bool) -> list[str]:
        if enabled:
            monkeypatch.setenv(HTTP_BODY_LOG_ENV, "1")
        else:
            monkeypatch.delenv(HTTP_BODY_LOG_ENV, raising=False)

        lines: list[str] = []
        import nuguard.redteam.target.client as client_mod

        monkeypatch.setattr(
            client_mod._log, "info", lambda msg, *a: lines.append(str(msg) % a)
        )
        async with respx.mock(base_url="http://app.test") as mock:
            mock.post("/chat").mock(
                return_value=httpx.Response(200, json={"content": "hello back"})
            )
            client = TargetAppClient(
                base_url="http://app.test",
                chat_response_key="content",
                chat_payload_template={"message": {"content": "{{message}}"}},
            )
            async with client:
                await client.send("probe payload", _session())
        return lines

    async def test_traces_request_response_and_extraction(self, monkeypatch) -> None:
        lines = await self._send(monkeypatch, enabled=True)
        joined = "\n".join(lines)
        assert '"message": {"content": "probe payload"}' in joined
        assert '"content": "hello back"' in joined
        assert "extracted text (response_key=content): hello back" in joined

    async def test_silent_when_disabled(self, monkeypatch) -> None:
        lines = await self._send(monkeypatch, enabled=False)
        assert not [ln for ln in lines if ln.startswith("HTTP ")]
