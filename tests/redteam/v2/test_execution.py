"""Phase 5 tests: objective → scenario synthesis, execution, kill-chain, outcome."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nuguard.redteam.v2.execution import KillChainState, ObjectiveOutcome, ObjectiveRunner
from nuguard.redteam.v2.planning import generate_objectives
from nuguard.redteam.v2.planning.objective_generator import ScenarioObjective
from nuguard.redteam.v2.scheduler import PhasedScheduler, RunContext, phase_from_int
from nuguard.redteam.v2.surface import AttackSurface
from nuguard.sbom.models import AiSbomDocument


def _sr(step_type: str = "INJECT", **kw) -> SimpleNamespace:
    """Build a fake StepResult exposing the attributes the summarizer reads."""
    defaults = dict(
        success_signal_found=False,
        canary_hits=[],
        policy_violations=[],
        llm_eval_confidence="",
        llm_eval_evidence="",
        tool_trace_hit=False,
        tool_trace_findings=[],
        artifact_hit=False,
        artifact_findings=[],
        golden_data_suppressed=False,
        response="",
    )
    defaults.update(kw)
    return SimpleNamespace(step=SimpleNamespace(step_type=step_type), **defaults)


class _FakeStatic:
    def __init__(self, results: list) -> None:
        self.results = results
        self.last_chain = None
        self.calls = 0

    async def run(self, chain):
        self.calls += 1
        self.last_chain = chain
        return chain, self.results, None


def _ctx(obj: ScenarioObjective) -> RunContext:
    return RunContext(
        objective=obj,
        phase=phase_from_int(obj.execution_phase),
        identity="id-test",
        fresh_identity=True,
    )


def _buildable_objective(runner: ObjectiveRunner, objectives: list[ScenarioObjective]) -> ScenarioObjective:
    for obj in objectives:
        if obj.builder_key is None:
            continue
        scenario = runner.synthesize_scenario(obj)
        if scenario is not None and getattr(scenario, "chain", None):
            return obj
    pytest.skip("no buildable static-chain objective for this SBOM")


def _runner(sbom: AiSbomDocument, executor) -> tuple[ObjectiveRunner, list[ScenarioObjective]]:
    surface = AttackSurface.from_sbom(sbom)
    objectives, _ = generate_objectives(surface)
    runner = ObjectiveRunner(
        sbom=sbom, profile=surface.profile, static_executor=executor
    )
    return runner, objectives


# ── synthesis ────────────────────────────────────────────────────────────────────
def test_synthesize_returns_static_chain(minimal_sbom_doc: AiSbomDocument) -> None:
    runner, objectives = _runner(minimal_sbom_doc, _FakeStatic([]))
    obj = _buildable_objective(runner, objectives)
    scenario = runner.synthesize_scenario(obj)
    assert scenario is not None
    assert scenario.chain is not None
    assert scenario.chain.steps


def test_strategy_only_objective_skipped(minimal_sbom_doc: AiSbomDocument) -> None:
    runner, objectives = _runner(minimal_sbom_doc, _FakeStatic([]))
    strategy = next((o for o in objectives if o.builder_key is None), None)
    assert strategy is not None
    outcome = asyncio.run(runner(_ctx(strategy)))
    assert outcome.status == "skipped_strategy_only"
    assert outcome.succeeded is False


# ── outcome summarisation ─────────────────────────────────────────────────────────
def test_canary_hit_is_success_and_critical(minimal_sbom_doc: AiSbomDocument) -> None:
    executor = _FakeStatic([_sr(canary_hits=["CANARY-9"])])
    runner, objectives = _runner(minimal_sbom_doc, executor)
    obj = _buildable_objective(runner, objectives)
    outcome = asyncio.run(runner(_ctx(obj)))
    assert outcome.status == "executed"
    assert outcome.succeeded is True
    assert outcome.critical is True
    assert any("canary" in e.lower() for e in outcome.evidence)


def test_no_signal_is_not_success(minimal_sbom_doc: AiSbomDocument) -> None:
    executor = _FakeStatic([_sr(response="I cannot help with that.")])
    runner, objectives = _runner(minimal_sbom_doc, executor)
    obj = _buildable_objective(runner, objectives)
    outcome = asyncio.run(runner(_ctx(obj)))
    assert outcome.succeeded is False
    assert outcome.critical is False


def test_high_severity_policy_violation_is_critical(minimal_sbom_doc: AiSbomDocument) -> None:
    pv = SimpleNamespace(policy_clause="restricted_topic", evidence="gave medical advice", severity="HIGH")
    executor = _FakeStatic([_sr(policy_violations=[pv])])
    runner, objectives = _runner(minimal_sbom_doc, executor)
    obj = _buildable_objective(runner, objectives)
    outcome = asyncio.run(runner(_ctx(obj)))
    assert outcome.succeeded is True
    assert outcome.critical is True


def test_warmup_steps_ignored_in_summary(minimal_sbom_doc: AiSbomDocument) -> None:
    # A canary hit on a WARMUP step must not count as success.
    executor = _FakeStatic([_sr(step_type="WARMUP", canary_hits=["X"])])
    runner, objectives = _runner(minimal_sbom_doc, executor)
    obj = _buildable_objective(runner, objectives)
    outcome = asyncio.run(runner(_ctx(obj)))
    assert outcome.succeeded is False


def test_executor_error_isolated(minimal_sbom_doc: AiSbomDocument) -> None:
    class _Boom:
        async def run(self, chain):
            raise RuntimeError("exec failed")

    runner, objectives = _runner(minimal_sbom_doc, _Boom())
    obj = _buildable_objective(runner, objectives)
    outcome = asyncio.run(runner(_ctx(obj)))
    assert outcome.status == "error"
    assert "exec failed" in outcome.reason


# ── kill-chain composition ────────────────────────────────────────────────────────
def test_killchain_never_injects_bracket_notation(
    minimal_sbom_doc: AiSbomDocument,
) -> None:
    """Kill-chain context must never be prepended verbatim as bracket notation.

    The old behaviour prepended ``[Context established earlier in this assessment: ...]``
    directly to the payload sent to the target application.  That text is
    nuguard-internal metadata and must not appear in messages sent to the AI app:
    it confuses the target and can trigger content-filter blocks.
    """
    executor = _FakeStatic([_sr(canary_hits=["CANARY-1"])])
    runner, objectives = _runner(minimal_sbom_doc, executor)
    obj = _buildable_objective(runner, objectives)

    # First run: no prior disclosures → payload unchanged.
    asyncio.run(runner(_ctx(obj)))
    first_chain = executor.last_chain
    first_adv = next(s for s in first_chain.steps if s.step_type not in ("WARMUP", "DISCOVER"))
    assert not first_adv.payload.startswith("[Context established earlier")
    assert runner.killchain.disclosures  # success is still recorded

    # Second run: prior success recorded.  Without a mutation_llm the chain is
    # returned unchanged — bracket notation must still never appear.
    asyncio.run(runner(_ctx(obj)))
    second_chain = executor.last_chain
    second_adv = next(s for s in second_chain.steps if s.step_type not in ("WARMUP", "DISCOVER"))
    assert not second_adv.payload.startswith("[Context established earlier")


def test_killchain_calls_llm_when_available(
    minimal_sbom_doc: AiSbomDocument,
) -> None:
    """When a mutation_llm is provided, _compose_kill_chain uses it to synthesise."""
    import asyncio as _asyncio

    synthesised_payload = "Contextual adversarial payload from LLM"

    class _FakeLLM:
        def __init__(self):
            self.calls: list[str] = []

        async def complete(self, prompt: str, **kwargs) -> str:
            self.calls.append(prompt)
            return synthesised_payload

    fake_llm = _FakeLLM()
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    objectives, _ = generate_objectives(surface)
    executor = _FakeStatic([_sr(canary_hits=["CANARY-2"])])
    runner = ObjectiveRunner(
        sbom=minimal_sbom_doc,
        profile=surface.profile,
        static_executor=executor,
        mutation_llm=fake_llm,
    )
    obj = _buildable_objective(runner, objectives)

    # First run: no prior disclosures → LLM not called, payload unchanged.
    _asyncio.run(runner(_ctx(obj)))
    assert not fake_llm.calls

    # Second run: prior success recorded → LLM called; synthesised payload used.
    _asyncio.run(runner(_ctx(obj)))
    assert fake_llm.calls, "mutation_llm.complete should have been called"
    adv = next(s for s in executor.last_chain.steps if s.step_type not in ("WARMUP", "DISCOVER"))
    assert adv.payload == synthesised_payload
    assert not adv.payload.startswith("[Context established earlier")


def test_killchain_disabled(minimal_sbom_doc: AiSbomDocument) -> None:
    executor = _FakeStatic([_sr(canary_hits=["C"])])
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    objectives, _ = generate_objectives(surface)
    runner = ObjectiveRunner(
        sbom=minimal_sbom_doc,
        profile=surface.profile,
        static_executor=executor,
        compose_kill_chains=False,
        killchain=KillChainState(disclosures=["prior win"]),
    )
    obj = _buildable_objective(runner, objectives)
    asyncio.run(runner(_ctx(obj)))
    adv = next(s for s in executor.last_chain.steps if s.step_type not in ("WARMUP", "DISCOVER"))
    assert not adv.payload.startswith("[Context established earlier")


# ── scheduler integration ─────────────────────────────────────────────────────────
def test_runner_drives_scheduler(minimal_sbom_doc: AiSbomDocument) -> None:
    executor = _FakeStatic([_sr(canary_hits=["CANARY-Z"])])
    runner, objectives = _runner(minimal_sbom_doc, executor)
    buildable = [
        o for o in objectives
        if o.builder_key and runner.synthesize_scenario(o) is not None
    ][:5]
    assert buildable

    sched = PhasedScheduler(concurrency=3)
    results = asyncio.run(sched.run(buildable, runner))
    assert len(results) == len(buildable)
    assert all(isinstance(r.result, ObjectiveOutcome) for r in results)
    # At least one executed objective should be flagged critical (canary hit).
    assert any(r.critical for r in results)
