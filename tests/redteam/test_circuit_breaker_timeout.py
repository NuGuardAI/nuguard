"""Tests for the run-level circuit breaker's handling of scenario timeouts.

Regression test for a gap where 3+ consecutive asyncio.TimeoutError results
from RedteamOrchestrator._run_scenarios never incremented the same
consecutive_unavailable counter that TargetUnavailableError does, so a
target that hangs (rather than erroring) could burn through every remaining
scenario instead of tripping the _ABORT_THRESHOLD=3 circuit breaker.
"""
from __future__ import annotations

import asyncio

import pytest

from nuguard.config import RedteamFindingTriggers
from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument


def _orchestrator(scenario_timeout: float) -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:3000",
        finding_triggers=RedteamFindingTriggers(),
        codegen_escalation_enabled=False,
        concurrency=1,
        scenario_timeout=scenario_timeout,
    )


def _scenario(chain_id: str) -> AttackScenario:
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
        scenario_type=ScenarioType.SKELETON_KEY,
        sbom_path=["node-1"],
    )
    scenario = AttackScenario(
        scenario_id=chain_id,
        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
        scenario_type=ScenarioType.SKELETON_KEY,
        title=f"Scenario {chain_id}",
        description="unit test scenario",
        target_node_ids=["node-1"],
        chain=chain,
    )
    scenario.attack_phase = 1
    return scenario


class _HangingExecutor:
    """Every chain run hangs well past the configured scenario_timeout."""

    async def run(self, chain: ExploitChain):
        await asyncio.sleep(10.0)
        raise AssertionError("should have been cancelled by the scenario timeout")


@pytest.mark.asyncio
async def test_three_consecutive_timeouts_trip_the_circuit_breaker() -> None:
    orchestrator = _orchestrator(scenario_timeout=0.01)
    scenarios = [_scenario(f"hang-{i}") for i in range(5)]
    executor = _HangingExecutor()

    _findings, _executed, records = await orchestrator._run_scenarios(
        scenarios, executor  # type: ignore[arg-type]
    )

    # The circuit breaker must have tripped: it should still open (recorded via
    # self._circuit_open) so a subsequent escalation pass doesn't re-hit the
    # hung target, and the remaining scenarios after the 3rd consecutive
    # timeout must have been skipped rather than each individually timing out.
    assert orchestrator._circuit_open is True
    statuses = [r.chain_status for r in records]
    assert statuses.count("timeout") == 3
    assert statuses.count("skipped") == 2
