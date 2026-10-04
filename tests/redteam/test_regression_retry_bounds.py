"""Deadline, error classification and slot-release regressions."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from nuguard.common.transport import TransportOutcome, classify_transport
from nuguard.config import NuGuardConfig, _flatten_yaml
from nuguard.redteam.defence_regressions.evaluator import DefenceRegressionEvaluator
from nuguard.redteam.defence_regressions.models import DefenceRegressionSpec
from nuguard.redteam.public_api import RedteamRunRequest
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession


@pytest.mark.asyncio
async def test_prepass_deadline_cancels_probe_and_logs_identity(caplog, monkeypatch):
    monkeypatch.setattr("nuguard.redteam.defence_regressions.evaluator._log.propagate", True)
    cancelled = asyncio.Event()

    async def blocked_send(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = AsyncMock()
    client.send.side_effect = blocked_send
    with caplog.at_level(logging.INFO):
        results, summary = await DefenceRegressionEvaluator(client).run(
            [DefenceRegressionSpec(name="probe-id", message="fixture-secret")],
            5,
            timeout_seconds=0.02,
            probe_timeout_seconds=1,
        )
    assert cancelled.is_set()
    assert results == []
    assert summary.timed_out
    assert client.send.call_count == 1
    assert "name='probe-id' variant=literal" in caplog.text
    assert "fixture-secret" not in caplog.text
    assert "remaining probes untested" in caplog.text


@pytest.mark.asyncio
async def test_probe_timeout_continues_to_next_spec():
    async def send(payload, *args, **kwargs):
        if payload == "slow":
            await asyncio.Event().wait()
        return "I cannot help with that request.", []

    client = AsyncMock()
    client.send.side_effect = send
    results, summary = await DefenceRegressionEvaluator(client).run(
        [
            DefenceRegressionSpec(name="a", message="slow"),
            DefenceRegressionSpec(name="b", message="fast"),
        ],
        0,
        timeout_seconds=1,
        probe_timeout_seconds=0.01,
    )
    assert results[0].send_error == "probe_timeout"
    assert not results[0].is_regression_hit
    assert results[1].refused
    assert summary.variants_failed == 1
    assert not summary.timed_out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        "I'm having difficulty connecting right now.",
        "[HTTP 503]",
        "[HTTP 400]",
        "[PROVIDER_BLOCKED]",
        '{"error":"failed"}',
        "",
    ],
)
async def test_failed_response_never_becomes_regression_hit(response):
    client = AsyncMock()
    client.send.return_value = (response, [])
    results, summary = await DefenceRegressionEvaluator(client).run(
        [DefenceRegressionSpec(name="a", message="x")],
        0,
    )
    assert results[0].send_error
    assert not results[0].is_regression_hit
    assert summary.variants_failed == 1


@pytest.mark.asyncio
async def test_external_cancellation_propagates():
    client = AsyncMock()
    client.send.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await DefenceRegressionEvaluator(client).run(
            [DefenceRegressionSpec(name="a", message="x")], 0
        )


@pytest.mark.parametrize(
    "response",
    [
        '{"error":{"code":"content_filter","message":"Please try again in a moment"}}',
        '{"error":{"innererror":{"code":"ResponsibleAIPolicyViolation"}}}',
        "[HTTP 503] [PROVIDER_BLOCKED]",
    ],
)
def test_explicit_provider_block_is_nonretryable(response):
    assert classify_transport(response) == TransportOutcome.HTTP_4XX


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status", [200, 400, 503])
async def test_provider_error_metadata_survives_extraction_without_retry(
    status, caplog, monkeypatch
):
    monkeypatch.setattr("nuguard.redteam.target.client._log.propagate", True)
    route = respx.post("http://target.test/api/chat").mock(
        return_value=httpx.Response(
            status, json={"error": {"code": "content_filter", "message": "fixture-secret"}}
        )
    )
    client = TargetAppClient(
        base_url="http://target.test", chat_path="/api/chat", max_concurrent_requests=1
    )
    with caplog.at_level(logging.DEBUG, logger="nuguard.redteam.target.client"):
        async with client:
            text, _ = await client.send(
                "x",
                AttackSession(session_id="probe", target_url="http://target.test", chain_id="a"),
            )
    assert route.call_count == 1
    assert classify_transport(text) == TransportOutcome.HTTP_4XX
    assert "fixture-secret" not in text
    assert "fixture-secret" not in caplog.text
    assert client._consecutive_errors == 0


@pytest.mark.asyncio
async def test_retry_sleep_releases_slot_and_ambiguous_error_is_capped(monkeypatch):
    client = TargetAppClient(base_url="http://target.test", max_concurrent_requests=1)
    client._send_impl = AsyncMock(return_value=("I'm having difficulty connecting right now.", []))
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)
        assert not client._request_sem.locked()
        async with client._request_sem:
            pass

    monkeypatch.setattr(asyncio, "sleep", sleep)
    await client.send(
        "x", AttackSession(session_id="probe", target_url="http://target.test", chain_id="a")
    )
    assert client._send_impl.call_count == 2
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_retry_wall_clock_deadline_includes_request():
    client = TargetAppClient(base_url="http://target.test", max_transient_hold_seconds=0.01)

    async def blocked_send(*args, **kwargs):
        await asyncio.Event().wait()

    client._send_impl = AsyncMock(side_effect=blocked_send)
    text, _ = await client.send(
        "x",
        AttackSession(session_id="probe", target_url="http://target.test", chain_id="a"),
        retry_transient=True,
    )
    assert text == "[REQUEST_ERROR: retry_deadline]"
    assert client._send_impl.call_count == 1


def test_deadline_config_and_public_request_roundtrip():
    cfg = NuGuardConfig(
        **_flatten_yaml(
            {"redteam": {"defence_regression_timeout": 120, "defence_regression_probe_timeout": 20}}
        )
    )
    assert cfg.redteam_defence_regression_timeout == 120
    assert cfg.redteam_defence_regression_probe_timeout == 20
    request = RedteamRunRequest(
        target_url="http://target.test",
        defence_regression_timeout=120,
        defence_regression_probe_timeout=20,
    )
    assert RedteamRunRequest.model_validate(request.model_dump(mode="json")) == request
    assert RedteamRunRequest(target_url="http://target.test").defence_regression_timeout == 180
    with pytest.raises(ValueError):
        RedteamRunRequest(target_url="http://target.test", defence_regression_probe_timeout=0)
    with pytest.raises(ValueError):
        NuGuardConfig(redteam_defence_regression_timeout=-1)
