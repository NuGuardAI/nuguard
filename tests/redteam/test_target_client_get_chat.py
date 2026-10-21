"""GET chat endpoints with query-string payloads and plain-text replies (issue #647)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from nuguard.common.http import to_query_params
from nuguard.redteam.target.client import (
    TargetAppClient,
    _parse_plain_text_reply,
)
from nuguard.redteam.target.session import AttackSession

_BASE = "http://localhost:9999"
_EXTRAS: dict[str, Any] = {"sessionId": "{{session_id}}", "userMessage": "{{message}}"}


def _session() -> AttackSession:
    return AttackSession(session_id="s1", target_url=_BASE, chain_id="c1")


async def _serve(
    client: TargetAppClient, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Route the client's HTTP calls to *handler* instead of the network."""
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )


def test_to_query_params_flattens_values() -> None:
    assert to_query_params(
        {
            "q": "hello world",
            "n": 3,
            "flag": True,
            "tags": ["a", 2],
            "nested": {"k": "v"},
            "skip": None,
        }
    ) == {
        "q": "hello world",
        "n": "3",
        "flag": "true",
        "tags": ["a", "2"],
        "nested": '{"k": "v"}',
    }
    assert to_query_params("not a dict") == {}


def test_parse_plain_text_reply() -> None:
    assert _parse_plain_text_reply("  hello there \n") == "hello there"
    assert _parse_plain_text_reply('{"response": "hi"}') == {"response": "hi"}
    assert _parse_plain_text_reply("{not json") == "{not json"


@pytest.mark.parametrize(
    ("method", "payload_in", "expected_method", "expected_in"),
    [
        ("GET", None, "GET", "query"),
        ("get", None, "GET", "query"),
        ("GET", "body", "GET", "body"),
        ("POST", None, "POST", "body"),
        ("POST", "query", "POST", "query"),
        ("PUT", None, "PUT", "body"),
        ("TRACE", None, "POST", "body"),
        ("GET", "weird", "GET", "query"),
    ],
)
def test_method_and_payload_location_defaults(
    method: str, payload_in: str | None, expected_method: str, expected_in: str
) -> None:
    client = TargetAppClient(base_url=_BASE, chat_method=method, chat_payload_in=payload_in)
    assert client._chat_method == expected_method
    assert client._chat_payload_in == expected_in


def test_default_client_still_posts_a_body() -> None:
    client = TargetAppClient(base_url=_BASE)
    assert client._chat_method == "POST"
    assert client._chat_payload_in == "body"


@pytest.mark.asyncio
async def test_send_get_renders_templated_extras_into_the_query_string() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="Your order ships Monday.\n")

    client = TargetAppClient(
        base_url=_BASE,
        chat_path="/customerSupportAgent",
        chat_method="GET",
        chat_payload_extras=_EXTRAS,
    )
    await _serve(client, handler)
    session = _session()

    text, _ = await client.send("where is my order?", session)

    assert text == "Your order ships Monday."
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/customerSupportAgent"
    assert dict(request.url.params) == {"sessionId": "s1", "userMessage": "where is my order?"}
    assert request.content == b""
    assert session.last_request_body == {"sessionId": "s1", "userMessage": "where is my order?"}
    await client.aclose()


@pytest.mark.asyncio
async def test_send_get_without_template_uses_payload_key_and_static_extras() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"response": "ok"})

    client = TargetAppClient(
        base_url=_BASE,
        chat_path="/ask",
        chat_payload_key="q",
        chat_method="GET",
        chat_payload_extras={"lang": "en"},
    )
    await _serve(client, handler)

    text, _ = await client.send("hi", _session())

    assert text == "ok"
    assert seen[0].method == "GET"
    assert dict(seen[0].url.params) == {"lang": "en", "q": "hi"}
    await client.aclose()


@pytest.mark.asyncio
async def test_send_post_with_query_payload_sends_no_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="done")

    client = TargetAppClient(
        base_url=_BASE, chat_method="POST", chat_payload_in="query", chat_payload_extras=_EXTRAS
    )
    await _serve(client, handler)

    text, _ = await client.send("hello", _session())

    assert text == "done"
    assert seen[0].method == "POST"
    assert dict(seen[0].url.params) == {"sessionId": "s1", "userMessage": "hello"}
    assert seen[0].content == b""
    await client.aclose()


@pytest.mark.asyncio
async def test_plain_text_reply_that_is_json_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text='{"response": "from json"}', headers={"content-type": "text/plain"}
        )

    client = TargetAppClient(base_url=_BASE, chat_method="GET", chat_payload_extras=_EXTRAS)
    await _serve(client, handler)

    text, _ = await client.send("hi", _session())

    assert text == "from json"
    await client.aclose()


@pytest.mark.asyncio
async def test_default_post_path_is_unchanged() -> None:
    client = TargetAppClient(base_url=_BASE, chat_payload_key="message")
    resp = httpx.Response(200, json={"response": "ok"}, request=httpx.Request("POST", _BASE))
    with (
        patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp) as post,
        patch.object(client._client, "request", new_callable=AsyncMock) as request,
    ):
        text, _ = await client.send("hello", _session())

    assert text == "ok"
    assert post.await_args is not None
    assert post.await_args.kwargs["json"] == {"message": "hello"}
    request.assert_not_awaited()
    await client.aclose()


@pytest.mark.asyncio
async def test_send_stream_get_uses_query_string_and_reads_plain_text() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="streamed hello", headers={"content-type": "text/plain"})

    client = TargetAppClient(
        base_url=_BASE,
        chat_path="/customerSupportAgent",
        chat_method="GET",
        chat_payload_extras=_EXTRAS,
    )
    await _serve(client, handler)

    chunks = [chunk async for chunk, _calls in client.send_stream("hello", _session())]

    assert "streamed hello" in "".join(chunks)
    assert seen[0].method == "GET"
    assert dict(seen[0].url.params) == {"sessionId": "s1", "userMessage": "hello"}
    assert seen[0].content == b""
    await client.aclose()
