"""Tests for campaign checkpoint save/restore (increment 3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuguard.common.run_checkpoint import CheckpointMismatchError, attack_scenario_signature
from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.campaign.branches import (
    BranchManager,
    BranchState,
    ObjectiveRequirements,
    RotationReason,
)
from nuguard.redteam.campaign.checkpoint import (
    CampaignCheckpointStore,
    DeferredObjective,
    campaign_signature,
    restore,
    target_fingerprint,
)
from nuguard.redteam.campaign.config import CampaignConfig
from nuguard.redteam.campaign.knowledge import KnowledgeItem, KnowledgeStore, Scope, TrustLevel
from nuguard.redteam.campaign.ledger import CoverageLedger, LedgerEntry, Status
from nuguard.redteam.campaign.scheduler import BudgetTracker
from nuguard.redteam.campaign.transport import Principal
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.session import AttackSession

PRIMARY = Principal.from_headers("primary", {"Authorization": "Bearer SECRET-TOKEN-123"})
SBOM = {"nodes": ["a"]}
SCOPE = Scope("dep", "/chat", "primary", PRIMARY.auth_scope)


def _mgr() -> BranchManager:
    return BranchManager(
        CampaignConfig(),
        lambda bid: AttackSession(session_id=bid, target_url="http://t", chain_id=bid),
    )


def _state():
    ledger, store = CoverageLedger(), KnowledgeStore()
    ledger.register(LedgerEntry("D01", "D01", "data", "read", "chat", "own", 4, status=Status.COMPLETED))
    ledger.register(LedgerEntry("T01", "T01", "tool", "misuse", "chat", "own", 5, status=Status.APPLICABLE))
    store.add(KnowledgeItem("id", "ACC-1", "ACC-1", TrustLevel.OBSERVED, SCOPE))
    mgr = _mgr()
    b = mgr.new_branch(PRIMARY)
    b.transport.headers["X-Secret"] = "SECRET-TOKEN-123"
    b.transport.cookies["sid"] = "COOKIE-SECRET"
    b.transport.session_context["conversation_id"] = "conv-9"
    b.session.add_turn("hello", "hi", [])
    b.baseline_done, b.state = True, BranchState.PROBING
    b.objective_history = ["D01", "T01"]
    b.contamination = {"instruction_override"}
    clock = [100.0]
    budget = BudgetTracker(max_requests=50, clock=lambda: clock[0])
    budget.spend(requests=12, llm_cost=0.4)
    clock[0] = 160.0
    return ledger, store, mgr, b, budget


def _save(tmp: Path, ledger, store, mgr, budget, **over):
    cps = CampaignCheckpointStore(tmp)
    kw = dict(
        sbom=SBOM, policy=None, target_fp=target_fingerprint("http://t", "/chat"),
        auth_fps={"primary": PRIMARY.auth_scope}, fixture_version="f1",
        ledger=ledger, branches=mgr, store=store, budget=budget,
        completed={"sig-D01"}, deferred=[DeferredObjective("sig-T01", 200.0, 1, "http_429")],
        pending_reproductions=[{"finding": "F1"}], write_objectives={"T01"},
        plan_cache={("fp", "D01", "slug"): [["turn"]]},
    )
    kw.update(over)
    return cps, cps.save("k", **kw)


async def _alive(_b) -> bool:
    return True


def _fresh():
    clock = [1000.0]
    return CoverageLedger(), KnowledgeStore(), _mgr(), BudgetTracker(max_requests=50, clock=lambda: clock[0])


@pytest.mark.asyncio
async def test_roundtrip_restores_ledger_branches_knowledge_budget_and_queue(tmp_path: Path) -> None:
    ledger, store, mgr, b, budget = _state()
    cps, path = _save(tmp_path, ledger, store, mgr, budget)
    payload = cps.load(path)
    assert payload is not None and payload["status"] == "running"

    l2, s2, m2, b2 = _fresh()
    rs = await restore(
        payload, sbom=SBOM, policy=None, target_fp=target_fingerprint("http://t", "/chat"),
        auth_fps={"primary": PRIMARY.auth_scope}, fixture_version="f1", ledger=l2, branches=m2,
        store=s2, budget=b2, principals={"primary": PRIMARY}, liveness=_alive,
    )
    assert l2.entries["D01"].status == Status.COMPLETED and l2.entries["T01"].status == Status.APPLICABLE
    assert s2.get(SCOPE, "baseline", "id", "ACC-1") is not None
    assert (b2.requests_used, b2.llm_cost_used) == (12, 0.4)
    assert b2.clock() - b2._t0 == pytest.approx(60.0)            # consumed time stays consumed
    rb = m2.branches[b.branch_id]
    assert rb.baseline_done and rb.contamination == {"instruction_override"}
    assert rb.transport.session_context == {"conversation_id": "conv-9"}
    assert [t.prompt for t in rb.session.turns] == ["hello"]
    assert rs.completed_signatures == {"sig-D01"}
    assert rs.deferred == [DeferredObjective("sig-T01", 200.0, 1, "http_429")]
    assert rs.pending_reproductions == [{"finding": "F1"}]
    assert rs.plan_cache == {("fp", "D01", "slug"): [["turn"]]}


def test_credentials_never_written_to_disk(tmp_path: Path) -> None:
    ledger, store, mgr, _b, budget = _state()
    _cps, path = _save(tmp_path, ledger, store, mgr, budget)
    raw = path.read_text()
    assert "SECRET-TOKEN-123" not in raw and "COOKIE-SECRET" not in raw and "X-Secret" not in raw
    assert PRIMARY.auth_scope in raw                                # only the fingerprint
    assert json.loads(raw)["campaign_checkpoint_version"] == 2


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    ledger, store, mgr, _b, budget = _state()
    _save(tmp_path, ledger, store, mgr, budget)
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"sbom": {"nodes": ["changed"]}}, "sbom/policy"),
        ({"target_fp": "other"}, "different target"),
        ({"auth_fps": {"primary": "rotated"}}, "auth scope"),
        ({"fixture_version": "f2"}, "fixture version"),
    ],
)
async def test_resume_refuses_mismatched_inputs(tmp_path: Path, override, match) -> None:
    ledger, store, mgr, _b, budget = _state()
    cps, path = _save(tmp_path, ledger, store, mgr, budget)
    kw = dict(
        sbom=SBOM, policy=None, target_fp=target_fingerprint("http://t", "/chat"),
        auth_fps={"primary": PRIMARY.auth_scope}, fixture_version="f1",
    )
    kw.update(override)
    l2, s2, m2, b2 = _fresh()
    with pytest.raises(CheckpointMismatchError, match=match):
        await restore(cps.load(path), ledger=l2, branches=m2, store=s2, budget=b2,
                      principals={"primary": PRIMARY}, liveness=_alive, **kw)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_expired_branch_is_rebuilt_from_permitted_setup_never_writes(tmp_path: Path) -> None:
    ledger, store, mgr, b, budget = _state()
    cps, path = _save(tmp_path, ledger, store, mgr, budget)

    async def dead(_b) -> bool:
        return False

    l2, s2, m2, b2 = _fresh()
    rs = await restore(
        cps.load(path), sbom=SBOM, policy=None, target_fp=target_fingerprint("http://t", "/chat"),
        auth_fps={"primary": PRIMARY.auth_scope}, fixture_version="f1", ledger=l2, branches=m2,
        store=s2, budget=b2, principals={"primary": PRIMARY}, liveness=dead,
    )
    assert rs.expired_branches == [b.branch_id]
    assert m2.branches[b.branch_id].state == BranchState.EXPIRED
    assert rs.rebuild_plans[b.branch_id] == ["D01"]                  # T01 was a write: not replayed


@pytest.mark.asyncio
async def test_retired_branches_stay_retired_and_unknown_principals_are_skipped(tmp_path: Path) -> None:
    ledger, store, mgr, b, budget = _state()
    mgr.retire(b, RotationReason.TURN_LIMIT)
    other = mgr.new_branch(Principal.from_headers("gone", {"Authorization": "Bearer X"}))
    cps, path = _save(tmp_path, ledger, store, mgr, budget)
    l2, s2, m2, b2 = _fresh()
    await restore(
        cps.load(path), sbom=SBOM, policy=None, target_fp=target_fingerprint("http://t", "/chat"),
        auth_fps={"primary": PRIMARY.auth_scope}, fixture_version="f1", ledger=l2, branches=m2,
        store=s2, budget=b2, principals={"primary": PRIMARY}, liveness=_alive,
    )
    assert m2.branches[b.branch_id].state == BranchState.RETIRED
    assert m2.branches[b.branch_id].retire_reason == RotationReason.TURN_LIMIT
    assert other.branch_id not in m2.branches                        # identity unavailable


def test_campaign_signature_includes_target_unlike_legacy_catalog_signature() -> None:
    def sc(node: str) -> AttackScenario:
        return AttackScenario(
            scenario_id=node, goal_type=GoalType.DATA_EXFILTRATION,
            scenario_type=ScenarioType.DIRECT_PII_EXTRACTION, title="same title", description="d",
            catalog_id="D01", target_node_ids=[node],
        )

    a, b = sc("agent-1"), sc("agent-2")
    assert attack_scenario_signature(a) == attack_scenario_signature(b)     # legacy collapse
    assert campaign_signature(a, "primary") != campaign_signature(b, "primary")
    assert campaign_signature(a, "primary") != campaign_signature(a, "other")
    assert campaign_signature(a, "primary") == campaign_signature(a, "primary")


def test_wrong_version_checkpoint_is_ignored(tmp_path: Path) -> None:
    cps = CampaignCheckpointStore(tmp_path)
    p = cps.path_for("k")
    p.write_text(json.dumps({"run_kind": "redteam", "campaign_checkpoint_version": 1}))
    assert cps.load(p) is None
