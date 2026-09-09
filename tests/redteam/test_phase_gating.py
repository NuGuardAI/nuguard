"""Tests for scenario dispatch grouping in RedteamOrchestrator._run_scenarios.

In concurrent mode (the platform default), scenarios are no longer gated by
the fine-grained 1-9 ``attack_phase`` barrier — they're dispatched in two
groups: all non-destructive scenarios, then all destructive scenarios
(``_is_destructive_scenario``). That's the only ordering invariant with a
real correctness reason (destructive scenarios must not run before
non-destructive ones that need intact account/data state). The strict
per-``attack_phase`` barrier is retained only for ``mode="progressive"``,
which needs it for its halt-on-severity gate (docs/claude-redteam-3.md §4)
— see ``tests/redteam/test_progressive_sequential_execution.py``.
"""
from __future__ import annotations

import asyncio

import pytest

from nuguard.config import RedteamFindingTriggers
from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.models.guided_conversation import GuidedConversation
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument


def _orchestrator(*, concurrency: int = 5, guided_concurrency: int = 1) -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:3000",
        finding_triggers=RedteamFindingTriggers(),
        codegen_escalation_enabled=False,
        concurrency=concurrency,
        guided_concurrency=guided_concurrency,
    )


def _scenario(chain_id: str, attack_phase: int, title: str | None = None) -> AttackScenario:
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
        title=title or f"Scenario {chain_id}",
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
async def test_destructive_never_starts_before_non_destructive_completes() -> None:
    """The one real ordering invariant in concurrent mode: destructive scenarios
    (title/description matching a destructive keyword) must not start until every
    non-destructive scenario has finished — regardless of attack_phase."""
    orchestrator = _orchestrator()
    scenarios = [
        _scenario("recon-a", attack_phase=1, title="Recon Probe A"),
        _scenario("recon-b", attack_phase=1, title="Recon Probe B"),
        # Deliberately an *earlier* attack_phase than the recon scenarios to
        # prove destructive-last is enforced independent of phase number.
        _scenario("destructive-fast", attack_phase=1, title="Cancel Subscription Attack"),
    ]
    events: list[str] = []
    executor = _FakeExecutor(events, slow_chain_ids={"recon-a", "recon-b"})

    await orchestrator._run_scenarios(scenarios, executor)  # type: ignore[arg-type]

    destructive_start_idx = events.index("destructive-fast:start")
    recon_a_end_idx = events.index("recon-a:end")
    recon_b_end_idx = events.index("recon-b:end")
    assert destructive_start_idx > recon_a_end_idx
    assert destructive_start_idx > recon_b_end_idx


@pytest.mark.asyncio
async def test_cross_phase_non_destructive_scenarios_run_concurrently() -> None:
    """Non-destructive scenarios in DIFFERENT attack_phases must overlap now —
    the fine-grained 1-9 phase barrier is loosened to a single non-destructive
    batch in concurrent mode (only destructive-last is a real invariant)."""
    orchestrator = _orchestrator()
    scenarios = [
        _scenario("phase1-a", attack_phase=1),
        _scenario("phase9-fast", attack_phase=9),
    ]
    events: list[str] = []
    executor = _FakeExecutor(events, slow_chain_ids={"phase1-a"})

    await orchestrator._run_scenarios(scenarios, executor)  # type: ignore[arg-type]

    # The fast phase-9 scenario should start (and can even finish) while the
    # slow phase-1 scenario is still running — no phase barrier between them.
    phase9_start_idx = events.index("phase9-fast:start")
    phase1a_end_idx = events.index("phase1-a:end")
    assert phase9_start_idx < phase1a_end_idx


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


def _guided_scenario(chain_id: str, attack_phase: int = 1) -> AttackScenario:
    return AttackScenario(
        scenario_id=chain_id,
        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
        scenario_type=ScenarioType.SKELETON_KEY,
        title=f"Guided {chain_id}",
        description="unit test guided scenario",
        target_node_ids=["node-1"],
        chain=None,
        guided_conversation=GuidedConversation(
            conversation_id=chain_id,
            goal_type=GoalType.PROMPT_DRIVEN_THREAT,
            goal_description="test goal",
        ),
    )


@pytest.mark.asyncio
async def test_guided_scenarios_use_their_own_concurrency_slot(monkeypatch) -> None:
    """A slow guided scenario must not block a fast static-chain scenario out of
    `sem` — guided scenarios are gated by the separate `guided_sem`
    (sized by `guided_concurrency`), not the shared HTTP semaphore."""
    events: list[str] = []

    async def _fake_run_guided_scenario(self, scenario, guided_executor, affected, variation_idx=0):
        events.append(f"{scenario.scenario_id}:start")
        await asyncio.sleep(0.05)
        events.append(f"{scenario.scenario_id}:end")
        return [], (scenario.title, scenario.goal_type.value, False), _record(scenario)

    def _record(scenario: AttackScenario):
        from nuguard.redteam.executor.orchestrator import ScenarioRecord
        return ScenarioRecord(
            title=scenario.title,
            goal_type=scenario.goal_type.value,
            scenario_type=scenario.scenario_type.value,
            description=scenario.description,
            impact_score=scenario.impact_score,
            affected="",
            chain_status="completed",
            had_finding=False,
        )

    monkeypatch.setattr(
        RedteamOrchestrator, "_run_guided_scenario", _fake_run_guided_scenario,
    )

    # concurrency=1 means the shared `sem` can only hold ONE scenario at a
    # time — if the guided scenario shared that semaphore, the fast static
    # scenario would be blocked behind it for the full 0.05s sleep.
    orchestrator = _orchestrator(concurrency=1, guided_concurrency=1)
    scenarios = [_guided_scenario("guided-a"), _scenario("static-fast", attack_phase=1)]
    static_executor = _FakeExecutor(events, slow_chain_ids=set())

    await orchestrator._run_scenarios(
        scenarios, static_executor, guided_executor=object(),  # type: ignore[arg-type]
    )

    # The fast static scenario must complete while the slow guided scenario
    # is still mid-flight (holding its own `guided_sem` slot) — proof they
    # aren't contending for the same semaphore. If they shared one semaphore
    # with concurrency=1, "static-fast" couldn't even start until "guided-a"
    # released its slot, so its "end" event would come after guided-a's.
    guided_end_idx = events.index("guided-a:end")
    static_end_idx = events.index("static-fast:end")
    assert static_end_idx < guided_end_idx


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
