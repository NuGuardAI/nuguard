"""Issue #656: inspect real probe request bodies through an offline HTTP transport."""

from __future__ import annotations

import copy
import json
from typing import Any
from uuid import UUID

import httpx
import pytest

from nuguard.common.endpoint_detection import live_probe
from nuguard.common.payload_templates import build_probe_payload, render_payload_template

BASE = "https://probe.test"
EXTRAS: dict[str, object] = {"userMessage": "{{message}}", "sessionId": "{{session_id}}"}


def _openapi() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "paths": {
            "/chat": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string"},
                                    },
                                }
                            },
                        }
                    }
                }
            }
        },
    }


async def _probe(
    extras: dict[str, Any] | None,
    *,
    mode: str = "blind",
    status: int = 200,
    response: dict[str, Any] | None = None,
    known_key: str | None = None,
) -> tuple[live_probe.ProbeResult | None, list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_openapi())
        body = json.loads(request.content)
        bodies.append(body)
        # Reproduce a target rejecting unrendered tokens and extraneous fields.
        if (
            "{{message}}" in request.content.decode()
            or "{{session_id}}" in request.content.decode()
        ):
            return httpx.Response(500, json={"error": "unrendered template"})
        if extras is not None and set(body) != set(extras):
            return httpx.Response(422, json={"detail": "unexpected request fields"})
        return httpx.Response(
            status,
            json=response if response is not None else {"response": "Actual reply"},
        )

    async with httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(respond)) as client:
        if mode == "openapi":
            result = await live_probe._try_openapi_detection(client, 1.0, None, extras)
        else:
            result = await live_probe._blind_probe(
                client,
                ["/chat"],
                [(known_key or "message", False), ("messages", True)],
                None,
                extras,
                known_payload_key=known_key,
            )
    return result, bodies


@pytest.mark.parametrize("mode", ["blind", "openapi"])
@pytest.mark.parametrize("key", ["userMessage", "message", "input"])
async def test_template_is_rendered_as_the_entire_probe_body(mode: str, key: str) -> None:
    extras = {key: "Say: {{message}}", "sessionId": "{{session_id}}", "enabled": True}
    original = copy.deepcopy(extras)
    result, bodies = await _probe(extras, mode=mode)
    assert result is not None and result.confirmed
    assert result.key == key and not result.is_list and result.value_template is None
    assert len(bodies) == 1
    assert bodies[0][key] == "Say: " + live_probe.TEST_MESSAGE
    assert UUID(bodies[0]["sessionId"]).version == 4
    assert bodies[0]["enabled"] is True
    assert set(bodies[0]) == set(extras)
    assert extras == original


@pytest.mark.parametrize("mode", ["blind", "openapi"])
async def test_nested_template_matches_first_turn_semantics(mode: str) -> None:
    extras = {
        "messages": [{"role": "user", "content": "{{message}}"}],
        "context": {
            "session": "{{session_id}}",
            "repeated": ["{{session_id}}"],
            "history": "{{history}}",
            "conversation": "{{conversation_id}}",
            "unknown": "{{keep_me}}",
            "count": 3,
            "empty": None,
        },
    }
    original = copy.deepcopy(extras)
    result, bodies = await _probe(extras, mode=mode)
    assert result is not None and result.confirmed and result.is_list
    assert result.key == "messages" and len(bodies) == 1
    body = bodies[0]
    assert body["context"]["history"] == "[]"
    assert body["context"]["conversation"] == ""
    assert body["context"]["unknown"] == "{{keep_me}}"
    assert body["context"]["repeated"] == [body["context"]["session"]]
    assert body == render_payload_template(
        extras,
        message=live_probe.TEST_MESSAGE,
        history="[]",
        session_id=body["context"]["session"],
        conversation_id="",
    )
    body["messages"][0]["content"] = "changed"
    assert extras == original


async def test_configured_key_does_not_overwrite_a_template() -> None:
    result, bodies = await _probe(EXTRAS, known_key="message")
    assert result is not None and result.confirmed and result.key == "userMessage"
    assert len(bodies) == 1 and "message" not in bodies[0]


@pytest.mark.parametrize("extras", [None, {}, {"tenant": "demo", "message": "old"}])
@pytest.mark.parametrize("value", ["hello", ["hello"], [{"role": "user", "content": "hello"}]])
def test_non_template_extras_preserve_the_existing_merge(extras: Any, value: object) -> None:
    original = copy.deepcopy(extras)
    assert build_probe_payload("message", value, extras, message="hello") == {
        **(extras or {}),
        "message": value,
    }
    assert extras == original


def test_session_only_tokens_keep_the_existing_runtime_opt_in_rule() -> None:
    extras = {"sessionId": "{{session_id}}"}
    assert build_probe_payload("message", "hello", extras, message="hello") == {
        "sessionId": "{{session_id}}",
        "message": "hello",
    }


def test_substitution_is_single_pass_and_does_not_mutate_config() -> None:
    message = 'A "quoted" value with \\ and {{session_id}}'
    original = copy.deepcopy(EXTRAS)
    first = build_probe_payload("message", message, EXTRAS, message=message)
    second = build_probe_payload("message", "next", EXTRAS, message="next")
    assert json.loads(json.dumps(first))["userMessage"] == message
    assert first["sessionId"] != second["sessionId"]
    assert EXTRAS == original


@pytest.mark.parametrize(
    "status,response",
    [
        (500, {"error": "provider unavailable"}),
        (200, {"error": "upstream unavailable"}),
        (200, {"session_id": "id", "status": "ok"}),
        (401, {"error": "unauthorized"}),
        (422, {"detail": "invalid payload"}),
    ],
)
@pytest.mark.parametrize("mode", ["blind", "openapi"])
async def test_rendered_template_does_not_turn_errors_into_confirmation(
    mode: str,
    status: int,
    response: dict[str, Any],
) -> None:
    result, bodies = await _probe(EXTRAS, mode=mode, status=status, response=response)
    assert len(bodies) == 1 and bodies[0]["userMessage"] == live_probe.TEST_MESSAGE
    assert result is None or not result.confirmed


async def test_template_probe_still_stops_on_rate_limiting() -> None:
    from nuguard.common.errors import TargetRateLimitedError

    with pytest.raises(TargetRateLimitedError):
        await _probe(EXTRAS, status=429)


async def test_static_extras_and_422_hint_controls_are_preserved() -> None:
    bodies: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "utterance" not in body:
            return httpx.Response(
                422,
                json={
                    "detail": [
                        {"loc": ["body", "utterance"], "type": "missing", "msg": "Field required"},
                    ]
                },
            )
        return httpx.Response(200, json={"response": "Actual reply"})

    async with httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(respond)) as client:
        result = await live_probe._blind_probe(
            client,
            ["/chat"],
            [("message", False)],
            None,
            {"tenant": "demo"},
        )
    assert result is not None and result.confirmed and result.key == "utterance"
    assert bodies == [
        {"tenant": "demo", "message": live_probe.TEST_MESSAGE},
        {"tenant": "demo", "utterance": live_probe.TEST_MESSAGE},
        {"tenant": "demo"},
    ]


async def test_bootstrap_uses_the_same_full_body_template(monkeypatch: pytest.MonkeyPatch) -> None:
    from nuguard.common.auth import AuthConfig
    from nuguard.common.bootstrap import AuthBootstrapper

    bodies: list[dict[str, Any]] = []
    original = copy.deepcopy(EXTRAS)
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if set(body) != set(EXTRAS) or "{{message}}" in request.content.decode():
            return httpx.Response(500, json={"error": "wrong probe body"})
        return httpx.Response(200, json={"response": "Actual reply"})

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    bootstrapper = AuthBootstrapper(
        target_url=BASE,
        endpoint="/chat",
        default_auth=AuthConfig(type="none"),
        startup_retries=0,
        probe_payload_extras=EXTRAS,
    )
    result = await bootstrapper._probe_once("default", {}, "none")
    assert result.status == "ok" and result.http_status_code == 200
    assert len(bodies) == 1
    assert bodies[0]["userMessage"] == "Hello, how can you help me?"
    assert UUID(bodies[0]["sessionId"]).version == 4
    assert EXTRAS == original


async def test_runtime_renderer_reuses_the_shared_implementation() -> None:
    from nuguard.common.payload_templates import contains_message_token
    from nuguard.redteam.target.client import (
        TargetAppClient,
        _contains_message_token,
        _render_payload_template,
    )
    from nuguard.redteam.target.session import AttackSession

    assert _contains_message_token is contains_message_token
    assert _render_payload_template is render_payload_template
    body = build_probe_payload("message", "hello", EXTRAS, message="hello")
    session = AttackSession(session_id=body["sessionId"], target_url=BASE, chain_id="fixture")
    client = TargetAppClient(BASE, chat_payload_extras=EXTRAS)
    try:
        assert client._build_templated_chat_payload("hello", session) == body
    finally:
        await client.aclose()
