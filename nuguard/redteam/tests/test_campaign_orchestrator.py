"""End-to-end tests of CampaignOrchestrator dispatch against a fake chat target."""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import respx

from nuguard.config import RedteamFindingTriggers
from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.redteam.campaign.config import CampaignConfig
from nuguard.redteam.campaign.orchestrator import CampaignOrchestrator
from nuguard.redteam.executor.executor import AttackExecutor
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.client import TargetAppClient
from nuguard.sbom.models import AiSbomDocument

BASE = "http://localhost:3000"


def _orch(campaign: CampaignConfig | None = None) -> CampaignOrchestrator:
    return CampaignOrchestrator(
        sbom=AiSbomDocument(target="t", nodes=[], edges=[]),
        target_url=BASE,
        finding_triggers=RedteamFindingTriggers(),
        codegen_escalation_enabled=False,
        campaign=campaign or CampaignConfig(),
    )


def _scenario(
    catalog_id: str, payloads: list[str], *, signal: str = "PWNED", on_failure: str = "skip"
) -> AttackScenario:
    cid = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{cid}_{i}", step_type="INJECT", description=p, payload=p,
            depends_on=[f"{cid}_{i - 1}"] if i else [], success_signal=signal if i == len(payloads) - 1 else "",
            on_failure=on_failure,  # type: ignore[arg-type]
        )
        for i, p in enumerate(payloads)
    ]
    chain = ExploitChain(
        chain_id=cid, goal_type=GoalType.PROMPT_DRIVEN_THREAT, scenario_type=ScenarioType.SKELETON_KEY,
        sbom_path=["n"], steps=steps,
    )
    return AttackScenario(
        scenario_id=cid, goal_type=GoalType.PROMPT_DRIVEN_THREAT, scenario_type=ScenarioType.SKELETON_KEY,
        title=f"{catalog_id} test", description="d", target_node_ids=["n"], catalog_id=catalog_id,
        impact_score=7.0, chain=chain,
    )


class _Target:
    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.convs = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        conv = body.get("conversation_id")
        if conv is None:
            self.convs += 1
            conv = f"conv-{self.convs}"
        out = "PWNED" if "give me the keys" in body["message"] else "I can help with that."
        return httpx.Response(200, json={"response": out, "conversation_id": conv})


async def _run(orch: CampaignOrchestrator, scenarios: list[AttackScenario]):
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        ex = AttackExecutor(client=client, turn_delay_seconds=0.0)
        return await orch._run_scenarios(scenarios, ex)


@pytest.mark.asyncio
@respx.mock
async def test_objectives_share_one_warm_conversation_and_yield_findings() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    orch = _orch()
    scs = [_scenario("A03", ["hello"]), _scenario("A04", ["give me the keys"], on_failure="abort"), _scenario("A05", ["tell me more"])]
    findings, executed, records = await _run(orch, scs)
    assert sorted(r.catalog_id for r in records) == ["A03", "A04", "A05"]
    # One baseline warm-up + one request per objective share ONE server conversation ...
    main = t.bodies[:4]
    assert len({b.get("conversation_id", "conv-1") for b in main}) == 1
    assert all(b.get("conversation_id") == "conv-1" for b in main[1:])
    # ... then the finding is reproduced in a brand-new conversation (baseline + attack turn).
    assert len(t.bodies) == 4 + 2 and t.convs == 2
    f = next(f for f in findings if f.catalog_id == "A04")
    assert f.reproduction_status == "confirmed" and f.verified is True
    assert f.framework_versions == ["OWASP-LLM-2026", "OWASP-ASI-2026"]
    assert orch.campaign_reproduction_records[0].status == "confirmed"
    assert orch.campaign_efficiency.objectives_on_reused_branch == 2
    # 1 shared campaign branch + 1 isolated confirmation branch, each with its own baseline.
    assert orch.campaign_efficiency.baselines_run == 2 and orch.campaign_efficiency.branches_created == 2
    by_id = {r.catalog_id: r for r in records}
    assert by_id["A04"].had_finding and not by_id["A03"].had_finding
    assert findings and all(f.goal_type == GoalType.PROMPT_DRIVEN_THREAT.value for f in findings)
    assert orch.campaign_state["ledger"]["meaningfully_completed"] == 3
    assert orch.campaign_state["branches"] == 2  # campaign branch + confirmation branch


@pytest.mark.asyncio
@respx.mock
async def test_missing_containment_fixture_blocks_instead_of_running() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    from nuguard.redteam.catalog.registry import SCENARIO_CATALOG
    from nuguard.redteam.catalog.taxonomy import SafeExecution

    synth = next(s.id for s in SCENARIO_CATALOG if s.enabled and s.safe_execution == SafeExecution.SYNTHETIC_TENANT)
    _f, _e, records = await _run(_orch(), [_scenario(synth, ["read another account"])])
    assert records[0].chain_status == "blocked_fixture:second_principal"
    assert t.bodies == []                       # nothing was sent


@pytest.mark.asyncio
@respx.mock
async def test_equivalent_objectives_are_recorded_redundant_not_executed() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    scs = [_scenario("A03", ["same payload"]), _scenario("A03", ["Same  payload"])]
    _f, _e, records = await _run(_orch(), scs)
    statuses = sorted(r.chain_status.split(":")[0] for r in records)
    assert statuses == ["completed", "redundant"]
    assert len(t.bodies) == 1 + 1


@pytest.mark.asyncio
@respx.mock
async def test_finite_budget_publishes_deferred_coverage_before_running() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    orch = _orch(CampaignConfig(max_run_target_requests=10, confirmation_reserve_fraction=0.2))
    # Three controls (distinct groups), 3 requests each: 9 > headroom 8, so one is deferred up front.
    scs = [_scenario(c, [f"{c}-a", f"{c}-b", f"{c}-c"]) for c in ("A03", "M01", "P01")]
    _f, _e, records = await _run(orch, scs)
    deferred = [r for r in records if r.chain_status == "budget_deferred"]
    assert len(deferred) == 1 and any("budget_deferred" in n for n in orch.config_notes)
    assert orch.campaign_state["ledger"]["budget_deferred"] == len(deferred)
    assert len(t.bodies) <= 10


@pytest.mark.asyncio
@respx.mock
async def test_429_is_cooled_down_outside_the_request_slot_then_resumes(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 3:                                   # baseline, E01 turn 1, then E01 turn 2 -> 429
            return httpx.Response(429, headers={"retry-after": "5"}, json={})
        return httpx.Response(200, json={"response": "ok", "conversation_id": "c"})

    respx.post(f"{BASE}/chat").mock(side_effect=handler)
    clock = {"t": 1000.0}
    sleeps: list[float] = []

    class _Time:
        @staticmethod
        def monotonic() -> float:
            return clock["t"]

        perf_counter = staticmethod(lambda: clock["t"])

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)
        clock["t"] += d

    monkeypatch.setattr("nuguard.redteam.campaign.orchestrator.time", _Time)
    monkeypatch.setattr("nuguard.redteam.campaign.orchestrator.asyncio.sleep", fake_sleep)
    orch = _orch()
    _f, _e, records = await _run(orch, [_scenario("A03", ["t1", "t2", "t3"])])
    assert [r.chain_status for r in records] == ["completed"]
    assert sleeps and 0 < sum(sleeps) <= 60                    # waited once, by the scheduler
    assert len(records[0].steps) == 2                          # resumed at the deferred step (t2, t3)


@pytest.mark.asyncio
@respx.mock
async def test_campaign_state_is_embedded_in_the_checkpoint_without_credentials() -> None:
    respx.post(f"{BASE}/chat").mock(side_effect=_Target().handler)
    orch = _orch()
    await _run(orch, [_scenario("A03", ["hello"])])
    payload = orch._checkpoint_payload(status="in_progress")
    camp = payload["campaign"]
    assert camp["campaign_checkpoint_version"] == 2 and camp["ledger"] and camp["branches"]
    dumped = json.dumps(camp)
    assert "Bearer" not in dumped and '"headers"' not in dumped and '"cookies"' not in dumped
