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


@pytest.mark.anyio
@respx.mock
async def test_bootstrap_accepts_a_plain_text_reply_without_a_json_warning() -> None:
    respx.route(method="GET", host="target.test", path=ENDPOINT).mock(
        return_value=httpx.Response(200, text="Hi, how can I help?")
    )

    report = await _bootstrapper(_EXTRAS, "GET").run()

    check = report.checks[0]
    assert check.status == "ok"
    assert check.body_warning == ""


@pytest.mark.anyio
@respx.mock
async def test_bootstrap_still_warns_when_a_non_json_reply_is_not_plain_text() -> None:
    respx.route(method="GET", host="target.test", path=ENDPOINT).mock(
        return_value=httpx.Response(
            200, text="<html>login</html>", headers={"content-type": "text/html"}
        )
    )

    report = await _bootstrapper(_EXTRAS, "GET").run()

    check = report.checks[0]
    assert check.status == "ok"
    assert "not valid JSON" in check.body_warning


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "payload_in", "probed"),
    [("GET", None, False), ("POST", "query", False), ("POST", "body", True)],
    ids=["get-query", "post-query", "post-body-control"],
)
async def test_orchestrator_probes_a_pinned_endpoint_only_for_the_default_post_body(
    monkeypatch: pytest.MonkeyPatch, method: str, payload_in: str, probed: bool
) -> None:
    """The startup payload-shape probe sends POSTs, so it must skip a pinned GET/query endpoint.

    The probe condition combines develop's ``not _chat_payload_key_explicit`` with this PR's
    ``not _pinned_request``; dropping either half changes one of these outcomes or develop's
    own precedence tests.
    """
    from unittest.mock import AsyncMock

    from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
    from nuguard.sbom.models import AiSbomDocument

    orchestrator = RedteamOrchestrator(
        sbom=AiSbomDocument(target="./app", nodes=[], edges=[]),
        target_url=TARGET,
        chat_path=ENDPOINT,
        chat_method=method,
        chat_payload_in=payload_in,
    )
    probe = AsyncMock()
    monkeypatch.setattr(orchestrator, "_maybe_probe_endpoints", probe)
    seen: dict[str, Any] = {}

    async def stop_after_resolution(**kwargs: Any) -> None:
        seen.update(kwargs)
        raise RuntimeError("captured startup resolution")

    monkeypatch.setattr(
        "nuguard.common.session_resolver.resolve_target_session", stop_after_resolution
    )
    with pytest.raises(RuntimeError, match="captured startup resolution"):
        await orchestrator.run()

    assert (probe.await_count == 1) is probed
    assert seen["chat_method"] == method and seen["chat_payload_in"] == payload_in
