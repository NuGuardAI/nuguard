"""Regression tests for #657: the run-level engagement guard must judge only
main-chat responses.

Root cause: ``RedteamOrchestrator._run_scenarios`` fed every scenario record
into ``_record_is_error_only`` — including direct-HTTP steps against
SBOM-derived REST paths.  Five REST-only scenarios answering 404/401/403
error envelopes therefore tripped the shared ``abort_event`` as "target not
engaged" while the main chat endpoint was healthy, skipping every remaining
chat scenario.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from nuguard.models.exploit_chain import (
    ExploitChain,
    ExploitStep,
    GoalType,
    ScenarioType,
)
from nuguard.redteam.executor.executor import AttackExecutor
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument

_THRESHOLD = 3
_ENVELOPE = '{"detail":"Not Found"}'


class _Client:
    """Fake client: chat replies ``chat_reply``; REST probes answer ``rest_status``."""

    def __init__(self, chat_reply: str, rest_status: int = 404) -> None:
        self.chat_reply = chat_reply
        self.rest_status = rest_status
        self._consecutive_errors = 0
        self._consecutive_endpoint_errors = 0
        self._request_sem = None
        self.chat_sends = 0

    def new_session(self, chain_id: str) -> AttackSession:
        return AttackSession(session_id=chain_id, target_url="http://fake", chain_id=chain_id)

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        pass

    def reset_circuit_breaker(self) -> None:
        pass

    async def send(
        self,
        payload: str,
        session: AttackSession,
        extra_headers=None,
        retry_transient: bool = False,
    ) -> tuple[str, list[dict]]:
        self.chat_sends += 1
        return self.chat_reply, []

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body=None,
        params=None,
        extra_headers=None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        return self.rest_status, _ENVELOPE, {}


def _rest_step(sid: str) -> ExploitStep:
    return ExploitStep(
        step_id=sid,
        step_type="INVOKE",
        description="rest probe",
        payload="",
        target_path="/api/auth/profile",
        http_method="GET",
        success_signal="",
        on_failure="skip",
    )


def _chat_step(sid: str) -> ExploitStep:
    return ExploitStep(
        step_id=sid,
        step_type="INJECT",
        description="chat step",
        payload="hello",
        success_signal="never matches",
        on_failure="skip",
    )


def _scenario(cid: str, steps: list[ExploitStep], title: str | None = None) -> AttackScenario:
    chain = ExploitChain(
        chain_id=cid,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        steps=steps,
    )
    return AttackScenario(
        scenario_id=cid,
        goal_type=chain.goal_type,
        scenario_type=chain.scenario_type,
        title=title or f"scenario {cid}",
        description="regression scenario",
        target_node_ids=["n1"],
        chain=chain,
    )


async def _run(client: _Client, scenarios: list[AttackScenario], concurrency: int = 1):
    orch = RedteamOrchestrator(
        sbom=AiSbomDocument(target="unit-test", nodes=[], edges=[]),
        target_url="http://fake",
        concurrency=concurrency,
        similar_miss_threshold=10_000,
        engagement_error_threshold=_THRESHOLD,
    )
    executor = AttackExecutor(client=cast(Any, client))
    _, _, records = await orch._run_scenarios(scenarios, executor, None)
    return orch, {r.title: r for r in records}


@pytest.mark.asyncio
@pytest.mark.parametrize("rest_status", [404, 401, 403])
async def test_rest_only_error_envelopes_do_not_abort_chat(rest_status: int) -> None:
    client = _Client(chat_reply="a healthy chat reply", rest_status=rest_status)
    rest = [_scenario(f"r{i}", [_rest_step("s1")]) for i in range(_THRESHOLD + 3)]
    chat = [_scenario(f"c{i}", [_chat_step("s1")]) for i in range(3)]
    orch, by_title = await _run(client, rest + chat)

    assert orch._not_engaged_detail in (None, "")
    assert all(by_title[f"scenario c{i}"].chain_status == "completed" for i in range(3))
    assert all(r.chain_status != "skipped" for r in by_title.values())


@pytest.mark.asyncio
async def test_mixed_scenarios_with_healthy_chat_do_not_abort() -> None:
    client = _Client(chat_reply="a healthy chat reply")
    mixed = [
        _scenario(f"m{i}", [_chat_step("s1"), _rest_step("s2")]) for i in range(_THRESHOLD + 2)
    ]
    tail = [_scenario("c1", [_chat_step("s1")])]
    orch, by_title = await _run(client, mixed + tail)

    assert orch._not_engaged_detail in (None, "")
    assert by_title["scenario c1"].chain_status == "completed"


@pytest.mark.asyncio
async def test_rest_scenarios_do_not_reset_chat_failure_count() -> None:
    """Interleaved REST-only scenarios neither add to nor clear the chat counter."""
    client = _Client(chat_reply=_ENVELOPE)
    order: list[AttackScenario] = []
    for i in range(_THRESHOLD):
        order.append(_scenario(f"c{i}", [_chat_step("s1")]))
        order.append(_scenario(f"r{i}", [_rest_step("s1")]))
    order.append(_scenario("late", [_chat_step("s1")]))
    orch, by_title = await _run(client, order)

    assert orch._not_engaged_detail
    assert by_title["scenario late"].chain_status == "not_engaged"


@pytest.mark.asyncio
async def test_confirmed_chat_failure_still_aborts_at_threshold() -> None:
    client = _Client(chat_reply=_ENVELOPE)
    chat = [_scenario(f"c{i}", [_chat_step("s1")]) for i in range(_THRESHOLD + 2)]
    orch, by_title = await _run(client, chat)

    assert orch._not_engaged_detail
    assert by_title[f"scenario c{_THRESHOLD}"].chain_status == "not_engaged"


@pytest.mark.asyncio
async def test_engagement_abort_gets_its_own_skip_reason() -> None:
    """Scenarios skipped by the engagement guard are not "circuit breaker open"."""
    from nuguard.redteam.report import _turns_cell_with_reason

    client = _Client(chat_reply=_ENVELOPE)
    chat = [_scenario(f"c{i}", [_chat_step("s1")]) for i in range(_THRESHOLD + 2)]
    orch, by_title = await _run(client, chat)

    skipped = by_title[f"scenario c{_THRESHOLD}"]
    assert skipped.chain_status == "not_engaged"
    cell = _turns_cell_with_reason("0/0", skipped.chain_status)
    assert "circuit breaker open" not in cell
    assert "not engaged" in cell


@pytest.mark.asyncio
async def test_real_circuit_breaker_still_reports_circuit_breaker_open() -> None:
    from nuguard.common.errors import TargetUnavailableError
    from nuguard.redteam.report import _turns_cell_with_reason

    class _DeadClient(_Client):
        async def send(self, payload, session, extra_headers=None, retry_transient=False):
            raise TargetUnavailableError("chat endpoint down")

    client = _DeadClient(chat_reply="")
    chat = [_scenario(f"c{i}", [_chat_step("s1")]) for i in range(6)]
    orch, by_title = await _run(client, chat)

    skipped = [r for r in by_title.values() if r.chain_status == "skipped"]
    assert skipped
    assert not orch._not_engaged_detail
    assert "circuit breaker open" in _turns_cell_with_reason("0/0", "skipped")


@pytest.mark.asyncio
async def test_concurrent_phases_rest_failures_do_not_stop_queued_chat_work() -> None:
    """Concurrency > 1 across the non-destructive then destructive phase."""
    client = _Client(chat_reply="a healthy chat reply")
    scenarios = [_scenario(f"r{i}", [_rest_step("s1")]) for i in range(_THRESHOLD + 3)]
    scenarios += [_scenario(f"c{i}", [_chat_step("s1")]) for i in range(4)]
    scenarios += [
        _scenario(f"d{i}", [_chat_step("s1")], title=f"Cancel order {i}") for i in range(3)
    ]
    scenarios += [
        _scenario(f"dr{i}", [_rest_step("s1")], title=f"Delete record {i}")
        for i in range(_THRESHOLD + 1)
    ]
    orch, by_title = await _run(client, scenarios, concurrency=3)

    assert orch._not_engaged_detail in (None, "")
    for i in range(4):
        assert by_title[f"scenario c{i}"].chain_status == "completed"
    for i in range(3):
        assert by_title[f"Cancel order {i}"].chain_status == "completed"
    assert all(r.chain_status not in ("skipped", "not_engaged") for r in by_title.values())
