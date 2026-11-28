"""Rate-limit evidence capture: hidden 429s, headers and timing (issue #766). Offline only."""
from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.redteam.executor.executor import AttackExecutor
from nuguard.redteam.scenarios.api_attacks import build_rate_limit_probe
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession


@pytest.mark.asyncio
async def test_invoke_endpoint_reports_hidden_429_and_only_rate_limit_headers() -> None:
    client = TargetAppClient(
        base_url="http://localhost:9999",
        max_429_retries=2,
        retry_429_backoff_base_seconds=0.01,
        retry_429_backoff_cap_seconds=0.02,
    )
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.text = '{"retryAfterMs": 1}'
    resp_429.headers = {
        "Retry-After": "1",
        "X-RateLimit-Limit": "5",
        "Set-Cookie": "session=secret",
    }
    resp_429.json = MagicMock(return_value={"retryAfterMs": 1})
    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.text = '{"ok": true}'
    resp_ok.headers = {}
    resp_ok.json = MagicMock(return_value={"ok": True})

    observation: dict = {}
    with patch("nuguard.redteam.target.client.asyncio.sleep", new_callable=AsyncMock):
        with patch.object(
            client._client, "request", new_callable=AsyncMock, side_effect=[resp_429, resp_ok]
        ):
            status, _, _ = await client.invoke_endpoint(
                path="/orders", method="POST", body={"x": 1}, observation=observation
            )

    assert status == 200  # the retry hides the 429 from the return value ...
    assert observation["retries_429"] == 1  # ... but not from the observation
    assert observation["headers"] == {"retry-after": "1", "x-ratelimit-limit": "5"}
    assert "set-cookie" not in observation["headers"]
    await client.aclose()


@pytest.mark.asyncio
async def test_invoke_endpoint_observation_is_optional() -> None:
    client = TargetAppClient(base_url="http://localhost:9999")
    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.text = "{}"
    resp_ok.headers = {}
    resp_ok.json = MagicMock(return_value={})
    with patch.object(client._client, "request", new_callable=AsyncMock, return_value=resp_ok):
        status, _, _ = await client.invoke_endpoint(path="/x", method="GET")
    assert status == 200
    await client.aclose()


class _ObservingClient:
    def new_session(self, chain_id: str) -> AttackSession:
        return AttackSession(session_id="s1", target_url="http://target", chain_id=chain_id)

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        pass

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
        observation: dict | None = None,
    ) -> tuple[int, str, dict]:
        if observation is not None:
            observation["retries_429"] = 1
            observation["headers"] = {"retry-after": "1"}
        return 200, "ok", {}


class _LegacyClient:
    """invoke_endpoint() without the ``observation`` parameter (older fakes)."""

    def new_session(self, chain_id: str) -> AttackSession:
        return AttackSession(session_id="s1", target_url="http://target", chain_id=chain_id)

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        pass

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        return 200, "ok", {}


async def _run_first_step(client: Any):
    scenario = build_rate_limit_probe(endpoint_id="ep", endpoint_name="Login", path="/login")
    chain = scenario.chain
    assert chain is not None
    session = AttackSession(session_id="s1", target_url="http://target", chain_id=chain.chain_id)
    executor = AttackExecutor(client=cast(Any, client))
    return await executor.run_step(chain.steps[0], session, chain)


@pytest.mark.asyncio
async def test_executor_records_timing_retried_429s_and_headers() -> None:
    result = await _run_first_step(_ObservingClient())
    assert result.http_status_code == 200
    obs = result.http_observation
    assert obs is not None
    assert isinstance(obs["sent_at"], float)
    assert obs["retries_429"] == 1
    assert obs["headers"] == {"retry-after": "1"}


@pytest.mark.asyncio
async def test_executor_still_works_with_clients_that_do_not_take_observation() -> None:
    result = await _run_first_step(_LegacyClient())
    assert result.http_status_code == 200
    obs = result.http_observation
    assert obs is not None
    assert isinstance(obs["sent_at"], float)
    assert obs["retries_429"] == 0
    assert obs["headers"] == {}
