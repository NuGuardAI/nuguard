"""Tests for hard phase-gating in RedteamOrchestrator._run_scenarios.

Covers the escalation-order fix: scenarios must be dispatched in ascending
``attack_phase`` batches, with each batch fully completing before the next
phase's batch is even created — not just sorted-then-fired-through-one-
asyncio.gather, which only made phase order a scheduling hint once
>= concurrency scenarios were in flight (see
nuguard/redteam/executor/orchestrator.py::_run_scenarios).
"""
from __future__ import annotations

import asyncio

import pytest

from nuguard.config import RedteamFindingTriggers
from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument


def _orchestrator() -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:3000",
        finding_triggers=RedteamFindingTriggers(),
        codegen_escalation_enabled=False,
        concurrency=5,
    )


def _scenario(chain_id: str, attack_phase: int) -> AttackScenario:
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
    scenario.attack_phase = attack_phase
    return scenario


class _FakeExecutor:
    """Records start/end events per chain; phase-1 chains are slow, phase-9 fast.

    If phase gating is broken (one flat asyncio.gather), the fast phase-9
    chain would start and finish before the slow phase-1 chains complete.
    With a real phase barrier, no phase-9 "start" event can appear before
    every phase-1 chain has logged its "end" event.
    """

    def __init__(self, events: list[str], slow_chain_ids: set[str]) -> None:
        self._events = events
        self._slow_chain_ids = slow_chain_ids

    async def run(self, chain: ExploitChain):
        self._events.append(f"{chain.chain_id}:start")
        if chain.chain_id in self._slow_chain_ids:
            await asyncio.sleep(0.05)
        chain.status = "completed"
        session = AttackSession(
            session_id=f"sess-{chain.chain_id}",
            target_url="http://localhost:3000",
            chain_id=chain.chain_id,
        )
        self._events.append(f"{chain.chain_id}:end")
        return chain, [], session


@pytest.mark.asyncio
async def test_phase_9_never_starts_before_phase_1_completes() -> None:
    orchestrator = _orchestrator()
    scenarios = [
        _scenario("phase1-a", attack_phase=1),
        _scenario("phase1-b", attack_phase=1),
        _scenario("phase9-fast", attack_phase=9),
    ]
    events: list[str] = []
    executor = _FakeExecutor(events, slow_chain_ids={"phase1-a", "phase1-b"})

    await orchestrator._run_scenarios(scenarios, executor)  # type: ignore[arg-type]

    phase9_start_idx = events.index("phase9-fast:start")
    phase1a_end_idx = events.index("phase1-a:end")
    phase1b_end_idx = events.index("phase1-b:end")
    assert phase9_start_idx > phase1a_end_idx
    assert phase9_start_idx > phase1b_end_idx


@pytest.mark.asyncio
async def test_intra_phase_scenarios_still_run_concurrently() -> None:
    """Two same-phase scenarios should overlap (both start before either ends)."""
    orchestrator = _orchestrator()
    scenarios = [
        _scenario("phase1-a", attack_phase=1),
        _scenario("phase1-b", attack_phase=1),
    ]
    events: list[str] = []
    executor = _FakeExecutor(events, slow_chain_ids={"phase1-a", "phase1-b"})

    await orchestrator._run_scenarios(scenarios, executor)  # type: ignore[arg-type]

    # Both starts must precede both ends when dispatched concurrently within a phase.
    start_idxs = [i for i, e in enumerate(events) if e.endswith(":start")]
    end_idxs = [i for i, e in enumerate(events) if e.endswith(":end")]
    assert max(start_idxs) < min(end_idxs)


@pytest.mark.asyncio
async def test_all_scenarios_produce_results_across_phases() -> None:
    """Every scenario across every phase batch still gets a result (none dropped)."""
    orchestrator = _orchestrator()
    scenarios = [
        _scenario("phase1-a", attack_phase=1),
        _scenario("phase5-a", attack_phase=5),
        _scenario("phase9-a", attack_phase=9),
    ]
    events: list[str] = []
    executor = _FakeExecutor(events, slow_chain_ids=set())

    findings, executed, records = await orchestrator._run_scenarios(scenarios, executor)  # type: ignore[arg-type]

    assert len(executed) == 3
    assert len(records) == 3
    assert {e[0] for e in executed} == {"Scenario phase1-a", "Scenario phase5-a", "Scenario phase9-a"}


@pytest.mark.asyncio
async def test_run_scenarios_emits_live_lifecycle_progress() -> None:
    updates: list[dict] = []
    orchestrator = _orchestrator()
    orchestrator._progress_sink = updates.append
    scenarios = [_scenario("phase1-a", attack_phase=1), _scenario("phase1-b", attack_phase=1)]

    await orchestrator._run_scenarios(scenarios, _FakeExecutor([], slow_chain_ids=set()))  # type: ignore[arg-type]

    assert updates[0] == {"kind": "plan", "scenarios_total": 2, "scenarios_completed": 0}
    assert [update["kind"] for update in updates].count("scenario_started") == 2
    completed = [update["scenarios_completed"] for update in updates if update["kind"] == "scenario_finished"]
    assert completed == [1, 2]
