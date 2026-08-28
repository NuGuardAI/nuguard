"""Tests for opt-in HTTP request/response body logging in TargetAppClient.

Gated by NUGUARD_LOG_HTTP_BODIES=1 (see nuguard.common.env_utils.env_bool).
Asserts against the module logger directly rather than caplog, since
nuguard.common.logging.get_logger() sets propagate=False.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.redteam.target import client as client_module
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession


def _make_session() -> AttackSession:
    return AttackSession(session_id="s1", target_url="http://localhost:9999", chain_id="c1")


def _mock_json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json = MagicMock(return_value=payload)
    resp.text = ""
    return resp


@pytest.mark.asyncio
async def test_send_logs_request_and_response_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")
    client = TargetAppClient(base_url="http://localhost:9999", chat_payload_key="message")
    session = _make_session()
    resp = _mock_json_response({"response": "hi"})

    with patch.object(client_module, "_log") as mock_log, \
         patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp):
        await client.send("hello", session)

    request_lines = [c for c in mock_log.info.call_args_list if c.args[0] == "HTTP %s %s%s: %s"]
    response_lines = [c for c in mock_log.info.call_args_list if c.args[0] == "HTTP Response %s %s%s: %s"]
    assert len(request_lines) == 1
    assert request_lines[0].args[1] == "POST"
    assert '"message": "hello"' in request_lines[0].args[4]
    assert len(response_lines) == 1
    assert response_lines[0].args[1] == 200

    await client.aclose()


@pytest.mark.asyncio
async def test_send_does_not_log_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NUGUARD_LOG_HTTP_BODIES", raising=False)
    client = TargetAppClient(base_url="http://localhost:9999", chat_payload_key="message")
    session = _make_session()
    resp = _mock_json_response({"response": "hi"})

    with patch.object(client_module, "_log") as mock_log, \
         patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp):
        await client.send("hello", session)

    assert not any(
        c.args and c.args[0] in ("HTTP %s %s%s: %s", "HTTP Response %s %s%s: %s")
        for c in mock_log.info.call_args_list
    )

    await client.aclose()


@pytest.mark.asyncio
async def test_invoke_endpoint_logs_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")
    client = TargetAppClient(base_url="http://localhost:9999")
    resp = _mock_json_response({"ok": True})
    resp.text = '{"ok": true}'

    with patch.object(client_module, "_log") as mock_log, \
         patch.object(client._client, "request", new_callable=AsyncMock, return_value=resp):
        await client.invoke_endpoint("/api/thing", method="POST", body={"a": 1})

    request_lines = [c for c in mock_log.info.call_args_list if c.args[0] == "HTTP %s %s%s: %s"]
    response_lines = [c for c in mock_log.info.call_args_list if c.args[0] == "HTTP Response %s %s%s: %s"]
    assert request_lines and '"a": 1' in request_lines[0].args[4]
    assert response_lines

    await client.aclose()


@pytest.mark.asyncio
async def test_send_raw_logs_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")
    client = TargetAppClient(base_url="http://localhost:9999")
    resp = _mock_json_response({"ok": True})

    with patch.object(client_module, "_log") as mock_log, \
         patch.object(client._client, "post", new_callable=AsyncMock, return_value=resp):
        await client.send_raw("/api/raw", {"x": 1})

    request_lines = [c for c in mock_log.info.call_args_list if c.args[0] == "HTTP %s %s%s: %s"]
    response_lines = [c for c in mock_log.info.call_args_list if c.args[0] == "HTTP Response %s %s%s: %s"]
    assert request_lines and request_lines[0].args[3] == " (raw)"
    assert response_lines and response_lines[0].args[3] == " (raw)"

    await client.aclose()
