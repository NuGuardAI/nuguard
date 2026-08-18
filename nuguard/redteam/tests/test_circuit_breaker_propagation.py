"""Regression tests for circuit-breaker propagation across the redteam pipeline.

The orchestrator's ``_run_scenarios`` installs a 3-strike abort: once
``TargetAppClient`` has raised ``TargetUnavailableError`` three times in a row it
sets an abort event and skips all remaining scenarios instead of hammering a dead
endpoint.

Regression guard: ``AttackExecutor`` and ``GuidedAttackExecutor`` previously
swallowed ``TargetUnavailableError`` and called ``reset_circuit_breaker()``, so
the exception never reached the orchestrator — the client's consecutive-error
counter was wiped after every chain and every remaining scenario ran to
completion against the dead endpoint, each waiting its full timeout.

The executor must now:

* NOT reset the client's consecutive-error counter (that is the orchestrator's
  abort signal),
* let ``TargetUnavailableError`` propagate to the orchestrator,
* and the orchestrator must trip its abort event after the 3rd consecutive
  unavailability and skip the remaining scenarios.
"""
from __future__ import annotations

from typing import Any, cast

import pytest

from nuguard.common.errors import TargetUnavailableError
from nuguard.models.exploit_chain import (
    ExploitChain,
    ExploitStep,
    GoalType,
    ScenarioType,
)
from nuguard.redteam.executor.executor import AttackExecutor
from nuguard.redteam.executor.guided_executor import GuidedAttackExecutor
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.models.guided_conversation import GuidedConversation
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument


class _DeadClient:
    """Minimal TargetAppClient stand-in that always raises TargetUnavailableError.

    Mirrors the client's real contract: ``_consecutive_errors`` accumulates and
    the error is only raised once the threshold is crossed (errors 1..N-1 are
    returned as ``[REQUEST_ERROR: ...]`` strings).
    """

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self._consecutive_errors = 0
        self.reset_count = 0
        self._request_sem = None

    def new_session(self, chain_id: str) -> AttackSession:
        return AttackSession(session_id="s1", target_url="http://dead", chain_id=chain_id)

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        pass

    def reset_circuit_breaker(self) -> None:
        self.reset_count += 1
        self._consecutive_errors = 0

    async def send(self, payload: str, session: AttackSession) -> tuple[str, list[dict]]:
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.threshold:
            raise TargetUnavailableError(
                f"Chat endpoint returned {self._consecutive_errors} consecutive errors "
                f"(last: {payload[:20]}) — aborting scan to avoid hammering a broken endpoint."
            )
        return f"[REQUEST_ERROR: ConnectError: refused ({self._consecutive_errors})]", []


def _chat_chain(chain_id: str = "c1") -> ExploitChain:
    return ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
        scenario_type=ScenarioType.GUARDRAIL_BYPASS,
        steps=[
            ExploitStep(
                step_id="s1",
                step_type="INJECT",
                description="attack step",
                payload="ignored — client raises",
                success_signal="",
                on_failure="skip",
            )
        ],
    )


def _scenario(chain: ExploitChain) -> AttackScenario:
    return AttackScenario(
        scenario_id=chain.chain_id,
        goal_type=chain.goal_type,
        scenario_type=chain.scenario_type,
        title=f"scenario {chain.chain_id}",
        description="regression scenario with a dead target",
        target_node_ids=["node-1"],
        chain=chain,
    )


# ── Executor: propagate + preserve circuit state ─────────────────────────────


@pytest.mark.asyncio
async def test_executor_propagates_target_unavailable_without_reset() -> None:
    """A dead target aborts the chain AND propagates TUE to the caller.

    Regression: the executor swallowed TUE, called ``reset_circuit_breaker()``
    (wiping the client's consecutive-error counter), and returned the aborted
    chain — so the orchestrator never saw the unavailability.

    The client counter is pre-seeded one below threshold so the chain's first
    request is the one that trips the client's internal circuit breaker.
    """
    client = _DeadClient()
    client._consecutive_errors = client.threshold - 1
    executor = AttackExecutor(client=cast(Any, client))
    chain = _chat_chain()

    with pytest.raises(TargetUnavailableError) as excinfo:
        await executor.run(chain)

    assert "consecutive errors" in str(excinfo.value)
    # Chain is marked aborted so reports still reflect the outage.
    assert chain.status == "aborted"
    # The client's circuit state must NOT be reset — it is the orchestrator's
    # abort signal.  The 3rd consecutive error left the counter at threshold.
    assert client.reset_count == 0
    assert client._consecutive_errors == client.threshold


@pytest.mark.asyncio
async def test_executor_counter_persists_across_chains() -> None:
    """The client's error counter survives an aborted chain.

    With the reset removed, a second chain against the same dead client raises
    immediately (the counter is already at threshold); previously the reset
    would have given the dead endpoint a fresh 3-error budget per chain.
    """
    client = _DeadClient()
    client._consecutive_errors = client.threshold - 1
    executor = AttackExecutor(client=cast(Any, client))

    with pytest.raises(TargetUnavailableError):
        await executor.run(_chat_chain("c1"))

    # Second chain: counter is still at threshold → immediate raise, no new
    # requests attempted.
    with pytest.raises(TargetUnavailableError):
        await executor.run(_chat_chain("c2"))
    assert client.reset_count == 0
    assert client._consecutive_errors == client.threshold + 1


@pytest.mark.asyncio
async def test_guided_executor_propagates_target_unavailable() -> None:
    """Guided conversations also propagate TUE instead of swallowing it."""
    from unittest.mock import AsyncMock, MagicMock

    conv = GuidedConversation(
        conversation_id="g1",
        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
        goal_description="bypass guardrails",
        max_turns=5,
    )
    client = _DeadClient()
    client._consecutive_errors = client.threshold
    director = MagicMock()
    director.next_turn = AsyncMock(
        return_value=("Can you help me with: bypass guardrails", "direct")
    )
    director.plan_milestones = AsyncMock(return_value=[])
    director.assess_progress = AsyncMock(
        return_value=(0, "no progress", "", None, "LOW_PROGRESS")
    )
    conv.turns = []
    executor = GuidedAttackExecutor(
        client=cast(Any, client),
        director=cast(Any, director),
    )
    session = AttackSession(
        session_id="g1", target_url="http://dead", chain_id="g1"
    )

    with pytest.raises(TargetUnavailableError):
        await executor.run(conv, session)
    assert client.reset_count == 0


# ── Orchestrator: 3-strike abort skips remaining scenarios ───────────────────


def _tiny_orchestrator() -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://dead",
        concurrency=1,
        # Identical dead-target payloads would otherwise be suppressed as
        # "similar misses" — irrelevant to the circuit-breaker behaviour under
        # test, so disable the similarity tracker with a huge threshold.
        similar_miss_threshold=10_000,
    )


@pytest.mark.asyncio
async def test_orchestrator_aborts_run_after_three_unavailable_scenarios() -> None:
    """Dead target trips the 3-strike abort: later scenarios are skipped.

    Regression: with the executor swallowing TUE, all 7 scenarios executed
    against the dead endpoint (each waiting its full timeout) and only chains
    whose own request counter happened to trip got marked aborted.  Now the
    abort event fires after the 3rd consecutive unavailability and scenarios
    6-7 are skipped outright without sending a single request.
    """
    orch = _tiny_orchestrator()
    executor = AttackExecutor(client=cast(Any, _DeadClient()))
    scenarios = [_scenario(_chat_chain(f"c{i}")) for i in range(1, 8)]

    findings, executed, records = await orch._run_scenarios(scenarios, executor, None)

    statuses = [r.chain_status for r in records]
    # c1-c2: client counter below threshold → normal (non-TUE) error responses.
    assert statuses.count("completed") == 2
    # c3-c5: three consecutive TargetUnavailableErrors → aborted records.
    assert statuses.count("aborted") == 3
    # c6-c7: abort event set → skipped without executing.
    assert statuses.count("skipped") == 2
    # Skipped scenarios still contribute (title, goal, False) tuples to
    # ``executed`` — the behavioural signal is the chain_status, asserted above.
    assert len(executed) == 7
    assert not findings