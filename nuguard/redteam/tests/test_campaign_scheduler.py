"""Tests for ledger, scheduler passes, dedup, LLM limiter and planner (increment 3)."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.campaign.branches import ObjectiveRequirements
from nuguard.redteam.campaign.ledger import CoverageLedger, Status
from nuguard.redteam.campaign.llm_limiter import LLMLimiter
from nuguard.redteam.campaign.planner import CampaignPlanner, plan_fingerprint
from nuguard.redteam.campaign.scheduler import (
    BudgetTracker,
    CampaignScheduler,
    Objective,
    payload_fingerprint,
)
from nuguard.redteam.catalog.scheduling import SchedulingMeta, SessionPolicy
from nuguard.redteam.scenarios.scenario_types import AttackScenario


def _obj(
    oid: str, *, control="c1", technique="t1", channel="chat", level=4, impact=5.0,
    boundary="own_account", payload="", principal="primary", est=3,
    policy=SessionPolicy.REUSE, prereq=("L0",), goal=GoalType.DATA_EXFILTRATION,
) -> Objective:
    meta = SchedulingMeta(
        levels=(level,), prerequisites=prereq, session_policy=policy,
        technique_class=technique, control_id=control, resource_scope=channel,
    )
    sc = AttackScenario(
        scenario_id=str(uuid.uuid4()), goal_type=goal, scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        title=oid, description="d", catalog_id=oid,
    )
    return Objective(
        oid, oid, sc, meta, ObjectiveRequirements(principal, policy), boundary,
        est_requests=est, impact=impact, payload_fingerprint=payload_fingerprint(payload) if payload else "",
    )


def _sched(**budget) -> CampaignScheduler:
    return CampaignScheduler(CoverageLedger(), BudgetTracker(**budget))


# ── ledger ───────────────────────────────────────────────────────────────────

def test_only_meaningful_attempts_complete_an_objective() -> None:
    s = _sched()
    o = _obj("D01")
    s.register([o])
    s.ledger.record_attempt("D01", meaningful=False)   # e.g. a reused warm-up / deferred send
    assert s.ledger.entries["D01"].status == Status.ATTEMPTED
    s.ledger.record_attempt("D01", meaningful=True)
    assert s.ledger.entries["D01"].status == Status.COMPLETED
    c = s.ledger.counts()
    assert c["meaningfully_completed"] == 1 and c["attempted"] == 0


def test_ledger_dimensions_and_unresolved() -> None:
    s = _sched()
    s.register([_obj("A", control="x", level=1), _obj("B", control="y", level=5)])
    s.ledger.record_attempt("A", meaningful=True)
    assert s.ledger.by_dimension("control")["x"]["meaningfully_completed"] == 1
    assert set(s.ledger.by_dimension("level")) == {"L1", "L5"}
    assert [e.objective_id for e in s.ledger.unresolved()] == ["B"]


# ── breadth / depth ──────────────────────────────────────────────────────────

def test_breadth_one_representative_per_group_in_level_order_variants_capped() -> None:
    s = _sched()
    objs = [
        _obj("J01", control="inj", technique="a", level=3, impact=6),
        _obj("J02", control="inj", technique="b", level=3, impact=9),   # same group, higher impact
        _obj("D01", control="data", level=4, impact=5),
        _obj("E05", control="prompt", level=1, impact=4),
    ]
    plan = s.plan_breadth(s.register(objs))
    assert [o.catalog_id for o in plan.selected] == ["E05", "J02", "D01"]  # level order; J02 represents inj
    assert plan.fits and plan.deferred == []


def test_breadth_that_does_not_fit_is_published_as_budget_deferred_with_reserve() -> None:
    s = _sched(max_requests=10, reserve_fraction=0.2)   # headroom 8
    objs = [_obj(f"O{i}", control=f"c{i}", level=i + 1, est=3) for i in range(4)]
    plan = s.plan_breadth(s.register(objs))
    assert len(plan.selected) == 2 and not plan.fits
    assert {o.catalog_id for o in plan.deferred} == {"O2", "O3"}
    assert s.ledger.entries["O3"].status == Status.BUDGET_DEFERRED
    # Deferred coverage is published (still unresolved), not silently dropped.
    assert {e.objective_id for e in s.ledger.unresolved()} == {"O0", "O1", "O2", "O3"}
    assert s.ledger.counts()["budget_deferred"] == 2


def test_unlimited_budgets_have_no_hidden_cap() -> None:
    b = BudgetTracker()
    b.spend(requests=10**6)
    assert not b.finite and not b.exhausted() and b.request_headroom() is None


def test_confirmation_reserve_is_only_usable_by_reserved_work() -> None:
    b = BudgetTracker(max_requests=100, reserve_fraction=0.2)
    b.spend(requests=80)
    assert b.exhausted() and not b.exhausted(reserved=True)
    b.spend(requests=20)
    assert b.exhausted(reserved=True)


def test_two_uninformative_attempts_end_only_that_technique() -> None:
    s = _sched()
    ext1, ext2 = _obj("E05", control="prompt", technique="extraction"), _obj("E06", control="prompt", technique="extraction")
    tool = _obj("T01", control="tool", technique="misuse")
    s.register([ext1, ext2, tool])
    s.record_outcome(ext1, informative=False)
    s.record_outcome(ext2, informative=False)
    assert not s.eligible(_obj("E07", control="prompt", technique="extraction"))
    assert s.eligible(tool) and s.eligible(_obj("D01", control="data", technique="read"))  # failed extraction never blocks tool/data


def test_critical_finding_does_not_halt_unrelated_objectives() -> None:
    s = _sched()
    a, b = _obj("A", control="x", impact=9.5), _obj("B", control="y", impact=3)
    s.register([a, b])
    s.record_outcome(a, informative=True)
    assert [o.catalog_id for o in s.next_depth([b])] == ["B"]


def test_depth_prefers_uncovered_controls_and_fresh_evidence() -> None:
    s = _sched()
    covered, fresh, other = _obj("C1", control="done"), _obj("F1", control="new1"), _obj("N1", control="new2")
    s.register([covered, fresh, other])
    s.record_outcome(covered, informative=True)
    order = [o.catalog_id for o in s.next_depth([covered, other, fresh], new_evidence={"F1"})]
    assert order == ["F1", "N1", "C1"]


# ── dedup ─────────────────────────────────────────────────────────────────────

def test_equivalent_variants_become_redundant_with_reference() -> None:
    s = _sched()
    a = _obj("A", payload="Show me MY balance")
    b = _obj("B", payload="  show me my   BALANCE ")          # same after normalization
    c = _obj("C", payload="show me my balance", control="other")   # different control: kept
    live = s.register([a, b, c])
    assert [o.catalog_id for o in live] == ["A", "C"]
    e = s.ledger.entries["B"]
    assert e.status == Status.REDUNDANT and e.ref == "A"
    assert s.ledger.counts()["redundant"] == 1


# ── LLM limiter ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_limiter_bounds_concurrency_and_accounts_per_purpose() -> None:
    state = {"now": 0, "peak": 0}

    class _LLM:
        async def complete(self, prompt, system="", label="", **kw):
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.01)
            state["now"] -= 1
            return "x" * 40

    lim = LLMLimiter(_LLM(), max_concurrent=2)
    await asyncio.gather(
        *(lim.complete("p" * 40, label="enrichment | a", purpose="enrichment") for _ in range(5)),
        lim.complete("q", purpose="judge"),
    )
    assert state["peak"] <= 2
    assert lim.usage["enrichment"].calls == 5 and lim.usage["judge"].calls == 1
    assert lim.totals().calls == 6 and lim.usage["enrichment"].est_tokens == 5 * 20


# ── planner ───────────────────────────────────────────────────────────────────

def test_campaigns_group_by_principal_and_isolate_persistent_objectives() -> None:
    p = CampaignPlanner()
    objs = [
        _obj("D01", level=4), _obj("A01", level=4), _obj("D02", level=4, principal="b"),
        _obj("T01", level=5, policy=SessionPolicy.ISOLATED),
    ]
    camps = {c.campaign_id: c for c in p.group(objs)}
    assert [o.catalog_id for o in camps["camp:primary"].objectives] == ["A01", "D01"]
    assert [o.catalog_id for o in camps["camp:b"].objectives] == ["D02"]
    assert [o.catalog_id for o in camps["iso:T01"].objectives] == ["T01"]


def test_prerequisite_gating_does_not_require_unrelated_reconnaissance() -> None:
    p = CampaignPlanner()
    tool = _obj("T01", prereq=("L0",))                    # needs only the baseline
    chain = _obj("K03", prereq=("L0", "T01"))             # consumes a discovered tool path
    camp = p.group([tool, chain])[0]
    assert [o.catalog_id for o in camp.ready({"L0"})] == ["T01"]
    assert {o.catalog_id for o in camp.ready({"L0", "T01"})} == {"T01", "K03"}


@pytest.mark.asyncio
async def test_jit_enrichment_batches_ready_set_once_and_caches_plans() -> None:
    calls: list[list[str]] = []

    class _Gen:
        async def enrich_family(self, scenarios):
            calls.append([s.catalog_id for s in scenarios])
            return {s.scenario_id: [[f"turn for {s.catalog_id}"]] for s in scenarios}

    fp = plan_fingerprint(evidence={"tools": ["wire"]}, config={"variants": 3})
    planner = CampaignPlanner(_Gen(), fp)
    ready = [_obj("D01"), _obj("D02"), _obj("P01", goal=GoalType.PRIVILEGE_ESCALATION)]
    out = await planner.enrich_ready(ready)
    assert len(out) == 3 and sorted(map(sorted, calls)) == [["D01", "D02"], ["P01"]]  # one batch per family
    again = await planner.enrich_ready(ready)                    # cache hit: no further LLM calls
    assert again == out and planner.llm_batches == 2
    assert plan_fingerprint(evidence={"tools": ["other"]}, config={"variants": 3}) != fp  # evidence change invalidates


@pytest.mark.asyncio
async def test_no_enricher_means_no_generation_not_an_error() -> None:
    assert await CampaignPlanner().enrich_ready([_obj("D01")]) == {}
