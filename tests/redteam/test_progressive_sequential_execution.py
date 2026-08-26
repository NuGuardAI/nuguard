"""Tests for redteam.mode: progressive strictly-sequential execution and the
halt_on_severity phase gate (docs/claude-redteam-3.md §4).
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


def _orchestrator(mode: str = "progressive", halt_on_severity: str = "none") -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:3000",
        finding_triggers=RedteamFindingTriggers(),
        codegen_escalation_enabled=False,
        concurrency=5,
        mode=mode,
        progressive_halt_on_severity=halt_on_severity,
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
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def run(self, chain: ExploitChain):
        self._events.append(f"{chain.chain_id}:start")
        await asyncio.sleep(0.02)
        chain.status = "completed"
        session = AttackSession(
            session_id=f"sess-{chain.chain_id}",
            target_url="http://localhost:3000",
            chain_id=chain.chain_id,
        )
        self._events.append(f"{chain.chain_id}:end")
        return chain, [], session


@pytest.mark.asyncio
async def test_progressive_mode_runs_same_phase_scenarios_one_at_a_time() -> None:
    """Unlike concurrent mode, same-phase scenarios must NOT overlap in progressive mode."""
    orchestrator = _orchestrator(mode="progressive")
    scenarios = [
        _scenario("phase1-a", attack_phase=1),
        _scenario("phase1-b", attack_phase=1),
    ]
    events: list[str] = []
    executor = _FakeExecutor(events)

    await orchestrator._run_scenarios(scenarios, executor)  # type: ignore[arg-type]

    # Each scenario's end must precede the next scenario's start (no overlap).
    assert events == [
        "phase1-a:start", "phase1-a:end",
        "phase1-b:start", "phase1-b:end",
    ]


@pytest.mark.asyncio
async def test_concurrent_mode_unaffected_by_progressive_fields() -> None:
    """Default mode keeps intra-phase concurrency even with the new fields present."""
    orchestrator = _orchestrator(mode="concurrent")
    scenarios = [
        _scenario("phase1-a", attack_phase=1),
        _scenario("phase1-b", attack_phase=1),
    ]
    events: list[str] = []
    executor = _FakeExecutor(events)

    await orchestrator._run_scenarios(scenarios, executor)  # type: ignore[arg-type]

    start_idxs = [i for i, e in enumerate(events) if e.endswith(":start")]
    end_idxs = [i for i, e in enumerate(events) if e.endswith(":end")]
    assert max(start_idxs) < min(end_idxs)
