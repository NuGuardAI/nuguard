"""Tests for AttackExecutor auth-refresh retry behavior on HTTP 401."""
from __future__ import annotations

from typing import Any, cast

import pytest

from nuguard.models.exploit_chain import (
    HTTP_2XX_SENTINEL,
    ExploitChain,
    ExploitStep,
    GoalType,
    ScenarioType,
)
from nuguard.redteam.executor.executor import AttackExecutor
from nuguard.redteam.target.session import AttackSession


class _FakeAuthSession:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def refresh_if_needed(self) -> bool:
        self.refresh_calls += 1
        return True

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer refreshed-token"}


class _FakeClient:
    def __init__(self) -> None:
        self.send_calls = 0
        self.invoke_calls = 0
        self.updated_headers: dict[str, str] = {}

    def new_session(self, chain_id: str) -> AttackSession:
        return AttackSession(session_id="s1", target_url="http://target", chain_id=chain_id)

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        if headers:
            self.updated_headers.update(headers)

    async def send(
        self,
        payload: str,
        session: AttackSession,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, list[dict]]:
        self.send_calls += 1
        if self.send_calls == 1:
            return "[HTTP 401]", []
        return "ok", []

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        self.invoke_calls += 1
        if self.invoke_calls == 1:
            return 401, "Unauthorized", {}
        return 200, "ok", {}


@pytest.mark.asyncio
async def test_chat_step_retries_once_after_401_refresh() -> None:
    client = _FakeClient()
    auth_session = _FakeAuthSession()
    executor = AttackExecutor(
        client=cast(Any, client),
        auth_session=cast(Any, auth_session),
    )

    chain = ExploitChain(
        chain_id="c1",
        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
        scenario_type=ScenarioType.GUARDRAIL_BYPASS,
        steps=[
            ExploitStep(
                step_id="s1",
                step_type="INJECT",
                description="chat step",
                payload="hello",
                success_signal="ok",
                on_failure="skip",
            )
        ],
    )

    _, results, _ = await executor.run(chain)

    assert len(results) == 1
    assert results[0].response == "ok"
    assert client.send_calls == 2
    assert auth_session.refresh_calls == 1
    assert client.updated_headers.get("Authorization") == "Bearer refreshed-token"


@pytest.mark.asyncio
async def test_direct_http_step_retries_once_after_401_refresh() -> None:
    client = _FakeClient()
    auth_session = _FakeAuthSession()
    executor = AttackExecutor(
        client=cast(Any, client),
        auth_session=cast(Any, auth_session),
    )

    chain = ExploitChain(
        chain_id="c2",
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        steps=[
            ExploitStep(
                step_id="s2",
                step_type="INJECT",
                description="direct endpoint step",
                payload="ignored",
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="skip",
                target_path="/api/orders/1",
                http_method="GET",
            )
        ],
    )

    _, results, _ = await executor.run(chain)

    assert len(results) == 1
    assert results[0].http_status_code == 200
    assert client.invoke_calls == 2
    assert auth_session.refresh_calls == 1
    assert client.updated_headers.get("Authorization") == "Bearer refreshed-token"


class _AlwaysUnauthorizedClient:
    """A direct-HTTP endpoint that always correctly rejects with 401."""

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
        return (
            401,
            "<html><h1>OWASP Juice Shop (Express ^4.22.1)</h1>"
            "<h2>401 UnauthorizedError</h2></html>",
            {},
        )


class _ExplodingResponseEvaluator:
    """Fails the test if the executor calls it — the deterministic 401 result
    must never reach the LLM judge for an HTTP_2XX_SENTINEL step."""

    async def evaluate(self, *args: Any, **kwargs: Any) -> dict:
        raise AssertionError(
            "LLMResponseEvaluator.evaluate() must not be called for a "
            "definitive non-2xx HTTP_2XX_SENTINEL result"
        )


@pytest.mark.asyncio
async def test_401_response_never_reaches_llm_eval_for_http_2xx_sentinel_step() -> None:
    """Regression: a correctly-enforced 401 auth check must not be flippable
    to 'attack succeeded' by an LLM judge misreading the error page's own
    branding text (e.g. 'OWASP Juice Shop (Express ^4.22.1)') as PII/data
    exposure. The LLM evaluator must not even be called once the 2xx check
    has already definitively failed."""
    client = _AlwaysUnauthorizedClient()
    executor = AttackExecutor(client=cast(Any, client))
    executor._response_evaluator = _ExplodingResponseEvaluator()  # type: ignore[assignment]

    chain = ExploitChain(
        chain_id="c3",
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        steps=[
            ExploitStep(
                step_id="s3",
                step_type="INJECT",
                description="direct endpoint step",
                payload="ignored",
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="abort",
                use_llm_eval=True,
                target_path="/api/basketitems",
                http_method="POST",
                strip_auth=True,
            )
        ],
    )

    _, results, _ = await executor.run(chain)

    assert len(results) == 1
    assert results[0].http_status_code == 401
    assert results[0].success_signal_found is False
