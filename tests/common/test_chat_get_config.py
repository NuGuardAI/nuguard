"""chat_method / chat_payload_in configuration for GET chat endpoints (issue #647)."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import ValidationError

from nuguard.common.session_resolver import resolve_target_session
from nuguard.config import BehaviorConfig, _flatten_yaml

_EXTRAS = {"sessionId": "{{session_id}}", "userMessage": "{{message}}"}
_TARGET: dict[str, object] = {
    "url": "http://localhost:8087",
    "endpoint": "/customerSupportAgent",
    "chat_method": "GET",
    "chat_payload_in": "query",
    "chat_payload_extras": _EXTRAS,
}


def test_shared_target_keys_reach_redteam_and_behavior() -> None:
    flat = _flatten_yaml({"target": dict(_TARGET), "redteam": {}, "behavior": {}})

    assert flat["redteam_chat_method"] == "GET"
    assert flat["redteam_chat_payload_in"] == "query"
    behavior = BehaviorConfig.model_validate(flat["behavior_config"])
    assert behavior.chat_method == "GET"
    assert behavior.chat_payload_in == "query"


def test_redteam_and_behavior_sections_override_the_shared_target() -> None:
    flat = _flatten_yaml(
        {
            "target": {"chat_method": "GET"},
            "redteam": {"chat_method": "POST", "chat_payload_in": "body"},
            "behavior": {"chat_method": "PUT"},
        }
    )

    assert flat["redteam_chat_method"] == "POST"
    assert flat["redteam_chat_payload_in"] == "body"
    assert flat["behavior_config"]["chat_method"] == "PUT"


def test_defaults_keep_post_with_a_body() -> None:
    behavior = BehaviorConfig()
    assert behavior.chat_method == "POST"
    assert behavior.chat_payload_in is None


@pytest.mark.parametrize("field", ["chat_method", "chat_payload_in"])
def test_unknown_values_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        BehaviorConfig.model_validate({field: "DELETE"})


@pytest.mark.anyio
@respx.mock
async def test_get_chat_endpoint_is_pinned_and_never_probed_with_post() -> None:
    chat = respx.route(method="GET", host="app.test", path="/customerSupportAgent").mock(
        return_value=httpx.Response(200, text="Hi, how can I help?")
    )
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    post = respx.post(url__regex=r".*").mock(return_value=httpx.Response(405))

    session_cfg, health_report = await resolve_target_session(
        target_url="http://app.test",
        sbom=None,
        auth_config=None,
        extra_headers={},
        chat_path="/customerSupportAgent",
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras=dict(_EXTRAS),
        chat_response_key=None,
        endpoint_explicit=True,
        chat_method="GET",
    )

    assert health_report.all_ok is True
    assert session_cfg.chat_path == "/customerSupportAgent"
    assert session_cfg.chat_method == "GET"
    assert chat.called
    assert post.call_count == 0
