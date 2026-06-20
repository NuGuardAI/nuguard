"""Phase 4 tests: phased scheduler ordering, locks, identities, and safety."""
from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace

from nuguard.redteam.v2.planning.objective_generator import (
    BehaviorCategory,
    ObjectiveIntent,
    ScenarioObjective,
)
from nuguard.redteam.v2.scheduler import (
    Phase,
    PhasedScheduler,
    RunContext,
    SafetyPolicy,
    assess,
)


def _obj(
    oid: str,
    *,
    phase: int = 4,
    state_impact: str = "none",
    safe_execution: str = "canary_only",
    resource_locks: tuple[str, ...] = (),
    reset_hooks: tuple[str, ...] = (),
    family: str = "direct_prompt_injection",
) -> ScenarioObjective:
    return ScenarioObjective(
        objective_id=oid,
        title=oid,
        intent=ObjectiveIntent.NEGATIVE,
        behavior_category=BehaviorCategory.PROHIBITED_CONTENT,
        family=family,
        technique_id="AIT-DIRECT-INJECTION-001",
        surface_node_ids=("n1",),
        surface_category="agents",
        policy_clauses=(),
        execution_phase=phase,
        state_impact=state_impact,
        resource_locks=resource_locks,
        required_fixtures=(),
        reset_hooks=reset_hooks,
        detectors=("refusal_absent",),
        required_evidence=("response_quote",),
        safe_execution=safe_execution,
        builder_key="structural_injection",
        mapped_scenario_ids=(),
        expected_behavior="refuse",
        success_signal="complied",
    )


def _id_factory():
    counter = itertools.count()
    return lambda: f"id-{next(counter)}"


# ── ordering ────────────────────────────────────────────────────────────────────
def test_phases_run_in_ascending_order() -> None:
    entry_phases: list[int] = []

    async def runner(ctx: RunContext):
        entry_phases.append(int(ctx.phase))
        return None

    objs = [_obj("a", phase=9), _obj("b", phase=4), _obj("c", phase=7), _obj("d", phase=3)]
    sched = PhasedScheduler(identity_factory=_id_factory())
    results = asyncio.run(sched.run(objs, runner))

    assert entry_phases == sorted(entry_phases)
    assert all(r.status == "completed" for r in results)


def test_high_impact_skipped_after_critical() -> None:
    async def runner(ctx: RunContext):
        # A phase-7 (data/identity) objective triggers a critical side effect.
        if ctx.phase == Phase.DATA_IDENTITY:
            return SimpleNamespace(critical=True)
        return SimpleNamespace(critical=False)

    objs = [
        _obj("data", phase=int(Phase.DATA_IDENTITY)),
        _obj("highimpact", phase=int(Phase.HIGH_IMPACT_DRY_RUN)),
        _obj("killchain", phase=int(Phase.AGENTIC_KILL_CHAIN)),
    ]
    sched = PhasedScheduler(identity_factory=_id_factory())
    results = {r.objective.objective_id: r for r in asyncio.run(sched.run(objs, runner))}

    assert results["data"].status == "completed"
    assert results["data"].critical is True
    assert results["highimpact"].status == "skipped_early_stop"
    assert results["killchain"].status == "skipped_early_stop"


def test_no_early_stop_when_disabled() -> None:
    async def runner(ctx: RunContext):
        return SimpleNamespace(critical=ctx.phase == Phase.DATA_IDENTITY)

    objs = [_obj("data", phase=7), _obj("hi", phase=9)]
    sched = PhasedScheduler(stop_on_critical=False, identity_factory=_id_factory())
    results = {r.objective.objective_id: r for r in asyncio.run(sched.run(objs, runner))}
    assert results["hi"].status == "completed"


# ── resource-lock serialization ─────────────────────────────────────────────────
def test_shared_lock_serializes() -> None:
    active = 0
    max_active = 0

    async def runner(ctx: RunContext):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    # Same phase, same lock → must serialize (max concurrency 1).
    objs = [_obj(f"o{i}", phase=4, resource_locks=("identity:u1",)) for i in range(3)]
    sched = PhasedScheduler(concurrency=5, identity_factory=_id_factory())
    asyncio.run(sched.run(objs, runner))
    assert max_active == 1


def test_independent_objectives_run_concurrently() -> None:
    active = 0
    max_active = 0

    async def runner(ctx: RunContext):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    objs = [_obj(f"o{i}", phase=4) for i in range(3)]  # no locks
    sched = PhasedScheduler(concurrency=5, identity_factory=_id_factory())
    asyncio.run(sched.run(objs, runner))
    assert max_active >= 2


# ── identities ──────────────────────────────────────────────────────────────────
def test_fresh_identity_per_objective() -> None:
    seen: list[str] = []

    async def runner(ctx: RunContext):
        seen.append(ctx.identity)

    objs = [_obj(f"o{i}", phase=4) for i in range(4)]
    sched = PhasedScheduler(identity_factory=_id_factory())
    asyncio.run(sched.run(objs, runner))
    assert len(set(seen)) == 4  # all distinct


def test_memory_objectives_reuse_identity() -> None:
    ids: dict[str, str] = {}

    async def runner(ctx: RunContext):
        ids[ctx.objective.objective_id] = ctx.identity

    objs = [
        _obj("m1", phase=6, state_impact="memory_write", family="memory_poisoning",
             resource_locks=("memory:u1",)),
        _obj("m2", phase=6, state_impact="memory_write", family="memory_poisoning",
             resource_locks=("memory:u1",)),
        _obj("m3", phase=6, state_impact="memory_write", family="memory_poisoning",
             resource_locks=("memory:u2",)),
    ]
    sched = PhasedScheduler(identity_factory=_id_factory())
    asyncio.run(sched.run(objs, runner))
    assert ids["m1"] == ids["m2"]      # same reuse key → shared identity
    assert ids["m1"] != ids["m3"]      # different reuse key → distinct identity


# ── safety ──────────────────────────────────────────────────────────────────────
def test_destructive_live_objective_blocked() -> None:
    async def runner(ctx: RunContext):
        raise AssertionError("runner must not be called for an unsafe objective")

    objs = [_obj("destroy", phase=9, state_impact="destructive", safe_execution="trace_only")]
    sched = PhasedScheduler(identity_factory=_id_factory())
    results = asyncio.run(sched.run(objs, runner))
    assert results[0].status == "skipped_safety"
    assert "destructive" in results[0].reason


def test_destructive_dry_run_allowed() -> None:
    async def runner(ctx: RunContext):
        return SimpleNamespace(critical=False)

    objs = [_obj("destroy", phase=9, state_impact="destructive", safe_execution="dry_run_tool")]
    sched = PhasedScheduler(identity_factory=_id_factory())
    results = asyncio.run(sched.run(objs, runner))
    assert results[0].status == "completed"


def test_assess_external_write_requires_trap() -> None:
    obj_live = _obj("x", state_impact="external_write", safe_execution="trace_only")
    obj_trap = _obj("y", state_impact="external_write", safe_execution="trap_endpoint")
    assert assess(obj_live, SafetyPolicy()).allowed is False
    assert assess(obj_trap, SafetyPolicy()).allowed is True


# ── robustness ──────────────────────────────────────────────────────────────────
def test_runner_error_isolated() -> None:
    async def runner(ctx: RunContext):
        if ctx.objective.objective_id == "boom":
            raise RuntimeError("kaboom")
        return None

    objs = [_obj("ok1", phase=4), _obj("boom", phase=4), _obj("ok2", phase=4)]
    sched = PhasedScheduler(identity_factory=_id_factory())
    results = {r.objective.objective_id: r for r in asyncio.run(sched.run(objs, runner))}
    assert results["boom"].status == "error"
    assert "kaboom" in results["boom"].reason
    assert results["ok1"].status == "completed"
    assert results["ok2"].status == "completed"


def test_reset_hooks_invoked_after_state_change() -> None:
    invoked: list[str] = []

    async def runner(ctx: RunContext):
        return None

    async def on_reset(hook: str, ctx: RunContext):
        invoked.append(hook)

    objs = [
        _obj("clean", phase=6, state_impact="memory_write", reset_hooks=("clear_memory",),
             family="memory_poisoning", resource_locks=("memory:u1",)),
        _obj("noreset", phase=4),  # state_impact none → no reset
    ]
    sched = PhasedScheduler(identity_factory=_id_factory())
    asyncio.run(sched.run(objs, runner, on_reset=on_reset))
    assert invoked == ["clear_memory"]
