"""chat_method / chat_payload_in reach the client builders and the bootstrap probe (#647)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from nuguard.common.auth import AuthConfig
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.common.http import resolve_chat_request
from nuguard.common.session_resolver import TargetSessionConfig
from nuguard.common.target_client_builder import (
    build_target_app_client,
    build_target_app_client_from_session,
)
from nuguard.redteam.target.client import TargetAppClient

TARGET = "http://target.test"
ENDPOINT = "/customerSupportAgent"
_EXTRAS: dict[str, Any] = {"sessionId": "{{session_id}}", "userMessage": "{{message}}"}


@pytest.mark.parametrize(
    ("method", "payload_in", "expected"),
    [
        (None, None, ("POST", "body")),
        ("get", None, ("GET", "query")),
        ("GET", "body", ("GET", "body")),
        ("POST", "query", ("POST", "query")),
        ("PATCH", None, ("PATCH", "body")),
        ("DELETE", None, ("POST", "body")),
        ("GET", "weird", ("GET", "query")),
    ],
)
def test_resolve_chat_request(
    method: str | None, payload_in: str | None, expected: tuple[str, str]
) -> None:
    assert resolve_chat_request(method, payload_in) == expected


@pytest.mark.asyncio
async def test_build_target_app_client_passes_method_and_location() -> None:
    client = build_target_app_client(
        TARGET, endpoint=ENDPOINT, chat_method="GET", payload_extras=_EXTRAS
    )
    assert isinstance(client, TargetAppClient)
    assert client._chat_method == "GET"
    assert client._chat_payload_in == "query"
    await client.aclose()


@pytest.mark.asyncio
async def test_build_target_app_client_defaults_to_post_body() -> None:
    client = build_target_app_client(TARGET, endpoint="/chat")
    assert isinstance(client, TargetAppClient)
    assert client._chat_method == "POST"
    assert client._chat_payload_in == "body"
    await client.aclose()


@pytest.mark.asyncio
async def test_build_from_session_passes_method_and_location() -> None:
    session = TargetSessionConfig(
        base_url=TARGET,
        chat_path=ENDPOINT,
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras=dict(_EXTRAS),
        chat_response_key=None,
        auth_session=None,
        chat_method="GET",
        chat_payload_in="query",
    )
    client = build_target_app_client_from_session(session)
    assert isinstance(client, TargetAppClient)
    assert client._chat_method == "GET"
    assert client._chat_payload_in == "query"
    await client.aclose()


def _bootstrapper(extras: dict[str, object] | None, method: str) -> AuthBootstrapper:
    return AuthBootstrapper(
        target_url=TARGET,
        endpoint=ENDPOINT,
        default_auth=AuthConfig(type="none"),
        run_id="test-run",
        startup_retries=0,
        probe_payload_extras=extras,
        chat_method=method,
    )


@pytest.mark.anyio
@respx.mock
async def test_bootstrap_probes_a_get_endpoint_with_rendered_query_params() -> None:
    route = respx.route(method="GET", host="target.test", path=ENDPOINT).mock(
        return_value=httpx.Response(200, text="Hi, how can I help?")
    )

    report = await _bootstrapper(_EXTRAS, "GET").run()

    assert report.all_ok is True
    assert route.call_count == 1
    params = dict(route.calls.last.request.url.params)
    assert params["userMessage"] == "Hello, how can you help me?"
    assert params["sessionId"]
    assert "{{" not in params["sessionId"]
    assert "message" not in params


@pytest.mark.anyio
@respx.mock
async def test_bootstrap_get_without_template_adds_the_payload_key() -> None:
    route = respx.route(method="GET", host="target.test", path=ENDPOINT).mock(
        return_value=httpx.Response(200, json={"response": "hello"})
    )

    report = await _bootstrapper({"lang": "en"}, "GET").run()

    assert report.all_ok is True
    params = dict(route.calls.last.request.url.params)
    assert params == {"lang": "en", "message": "Hello, how can you help me?"}


@pytest.mark.anyio
@respx.mock
async def test_bootstrap_default_still_posts_a_json_body() -> None:
    post = respx.route(method="POST", host="target.test", path=ENDPOINT).mock(
        return_value=httpx.Response(200, json={"response": "hello"})
    )
    get = respx.route(method="GET", host="target.test", path=ENDPOINT).mock(
        return_value=httpx.Response(405)
    )

    report = await _bootstrapper(None, "POST").run()

    assert report.all_ok is True
    assert post.call_count == 1
    assert get.call_count == 0
