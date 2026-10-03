"""Tests for fresh-session reproduction, minimization and recovery (increment 4)."""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import respx

from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.redteam.campaign.branches import BranchManager, ObjectiveRequirements
from nuguard.redteam.campaign.config import CampaignConfig
from nuguard.redteam.campaign.confirmation import (
    Candidate,
    ConfirmationRunner,
    ReproStatus,
    SetupItem,
)
from nuguard.redteam.campaign.executor import CampaignExecutor
from nuguard.redteam.campaign.knowledge import KnowledgeStore, Scope
from nuguard.redteam.campaign.scheduler import BudgetTracker
from nuguard.redteam.campaign.transport import Principal
from nuguard.redteam.campaign.transport.capabilities import SessionMode, TargetCapabilities
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession

BASE = "http://t"
P = Principal.from_headers("primary", {"Authorization": "Bearer P"})
SCOPE = Scope("dep", "/chat", "primary", P.auth_scope)
CAPS = TargetCapabilities(SessionMode.SERVER_SESSION, supports_reset=True, parallel_branches=True, source="test")


class _Target:
    """Server-session target: promoting to admin in a conversation unlocks the secret there."""

    def __init__(self, vulnerable: bool = True, lingering: bool = False) -> None:
        self.vulnerable, self.lingering = vulnerable, lingering
        self.admin: set[str] = set()
        self.convs = 0
        self.log: list[tuple[str, str]] = []   # (conversation_id, message)
        self.global_marker = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        conv = body.get("conversation_id")
        if conv is None:
            self.convs += 1
            conv = f"conv-{self.convs}"
        msg = body["message"]
        self.log.append((conv, msg))
        out = "ok"
        if "set role admin" in msg and self.vulnerable:
            self.admin.add(conv)
            out = "role updated"
            if self.lingering:
                self.global_marker = True
        elif "show secrets" in msg and conv in self.admin:
            out = "SECRET-DATA"
        elif "greet" in msg and self.global_marker:
            out = "hello CAMPAIGN-MARKER"
        return httpx.Response(200, json={"response": out, "conversation_id": conv})


def _step(i: int, payload: str, signal: str = "") -> ExploitStep:
    return ExploitStep(
        step_id=f"s{i}-{uuid.uuid4().hex[:6]}", step_type="INJECT", description=payload,
        payload=payload, success_signal=signal, on_failure="skip",
    )


def _candidate(payloads: list[str], **kw) -> Candidate:
    steps = [_step(i, p, "SECRET-DATA" if i == len(payloads) - 1 else "") for i, p in enumerate(payloads)]
    chain = ExploitChain(
        chain_id="c", goal_type=GoalType.PRIVILEGE_ESCALATION, scenario_type=ScenarioType.PRIVILEGE_CHAIN,
        sbom_path=[], steps=steps,
    )
    sc = AttackScenario(
        scenario_id="s", goal_type=GoalType.PRIVILEGE_ESCALATION, scenario_type=ScenarioType.PRIVILEGE_CHAIN,
        title="t", description="d", catalog_id="A03", chain=chain,
    )
    return Candidate("A03", sc, P, ObjectiveRequirements("primary"), SCOPE, **kw)


async def _runner(client, caps=CAPS, **kw) -> tuple[ConfirmationRunner, CampaignExecutor]:
    mgr = BranchManager(
        CampaignConfig(), lambda bid: AttackSession(session_id=bid, target_url=BASE, chain_id=bid)
    )
    ex = CampaignExecutor(client, mgr, store=KnowledgeStore())
    cfg = kw.pop("config", CampaignConfig())
    return ConfirmationRunner(ex, caps, cfg, **kw), ex


@pytest.mark.asyncio
@respx.mock
async def test_history_dependent_exploit_replays_prerequisites_in_a_fresh_conversation() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client)
        # The original run used conv-X; replay must not reuse it.
        rec = await runner.confirm(_candidate(["hello there", "set role admin", "show secrets"],
                                              original_evidence="SECRET-DATA seen originally"))
    assert rec.status == ReproStatus.CONFIRMED
    assert rec.minimal_turns == ["set role admin", "show secrets"]       # irrelevant greeting minimized away
    assert t.convs >= 2 and len({c for c, _ in t.log}) == t.convs        # every trial = a new conversation
    assert rec.original_evidence == "SECRET-DATA seen originally"
    assert rec.trials >= 2


@pytest.mark.asyncio
@respx.mock
async def test_final_payload_alone_is_not_sufficient_so_prerequisite_is_kept() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client)
        rec = await runner.confirm(_candidate(["set role admin", "show secrets"]))
    assert rec.status == ReproStatus.CONFIRMED and "set role admin" in rec.minimal_turns


@pytest.mark.asyncio
@respx.mock
async def test_not_reproduced_is_context_dependent_and_keeps_original_evidence() -> None:
    t = _Target(vulnerable=False)
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client)
        rec = await runner.confirm(_candidate(["set role admin", "show secrets"], original_evidence="orig"))
    assert rec.status == ReproStatus.NOT_REPRODUCED and rec.context_dependent
    assert rec.original_evidence == "orig"


@pytest.mark.asyncio
@respx.mock
async def test_no_reliable_reset_blocks_instead_of_pretending() -> None:
    respx.post(f"{BASE}/chat").mock(side_effect=_Target().handler)
    caps = TargetCapabilities(SessionMode.SERVER_SESSION, supports_reset=False, parallel_branches=True, source="op")
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client, caps)
        rec = await runner.confirm(_candidate(["set role admin", "show secrets"]))
        assert rec.status == ReproStatus.BLOCKED and rec.reason == "no_reliable_reset_or_isolation"
        # Behaviourally-disproved isolation also blocks.
        bad = TargetCapabilities(SessionMode.SERVER_SESSION, True, True, "t", isolation_verified=False)
        runner2, _ = await _runner(client, bad)
        assert (await runner2.confirm(_candidate(["a", "b"]))).status == ReproStatus.BLOCKED
        # And the operator can disable confirmation outright.
        runner3, _ = await _runner(client, config=CampaignConfig(confirm_in_fresh_sessions=False,
                                                                 target_supports_session_reset=None))
        assert (await runner3.confirm(_candidate(["a"]))).reason == "confirmation_disabled"


@pytest.mark.asyncio
@respx.mock
async def test_stateless_target_only_confirms_single_turn_findings() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    caps = TargetCapabilities(SessionMode.STATELESS, True, True, "t")
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client, caps)
        multi = await runner.confirm(_candidate(["set role admin", "show secrets"]))
    assert multi.status == ReproStatus.BLOCKED and "history" in multi.reason


@pytest.mark.asyncio
@respx.mock
async def test_writes_in_setup_are_never_replayed() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    setup = [
        SetupItem("D01", (_step(0, "list my accounts"),)),
        SetupItem("T01", (_step(1, "transfer 1000 to ACC-9"),), write=True),
    ]
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client)
        rec = await runner.confirm(_candidate(["set role admin", "show secrets"], setup=setup))
    assert rec.setup_replayed == ["D01"] and rec.setup_skipped_writes == ["T01"]
    assert not any("transfer" in m for _, m in t.log)


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_budget_yields_not_attempted() -> None:
    respx.post(f"{BASE}/chat").mock(side_effect=_Target().handler)
    b = BudgetTracker(max_requests=10)
    b.spend(requests=10)
    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client, budget=b)
        rec = await runner.confirm(_candidate(["set role admin", "show secrets"]))
    assert rec.status == ReproStatus.NOT_ATTEMPTED and rec.reason == "budget_exhausted"


@pytest.mark.asyncio
@respx.mock
async def test_deterministic_evidence_skips_judge_but_still_replays() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    calls = {"n": 0}

    async def judge(_rec) -> bool:
        calls["n"] += 1
        return True

    async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
        runner, _ = await _runner(client, judge=judge)
        det = await runner.confirm(_candidate(["set role admin", "show secrets"],
                                              evidence_kind="canary", deterministic=True))
        assert det.status == ReproStatus.CONFIRMED and det.judge_calls == 0 and det.effect_verified
        amb = await runner.confirm(_candidate(["set role admin", "show secrets"]))
    assert amb.status == ReproStatus.CONFIRMED and amb.judge_calls >= 1 and calls["n"] == amb.judge_calls
    assert not amb.effect_verified


@pytest.mark.asyncio
@respx.mock
async def test_recovery_control_detects_lingering_campaign_effect() -> None:
    clean, dirty = _Target(), _Target(lingering=True)
    for target, expected in ((clean, True), (dirty, False)):
        with respx.mock:
            respx.post(f"{BASE}/chat").mock(side_effect=target.handler)
            async with TargetAppClient(base_url=BASE, chat_path="/chat", timeout=5.0) as client:
                runner, _ = await _runner(client)
                if target.lingering:   # a persistent change made during the campaign
                    await runner.confirm(_candidate(["set role admin", "show secrets"]))
                ok, why = await runner.verify_recovery(
                    P, SCOPE, ("CAMPAIGN-MARKER",), question="please greet me"
                )
            assert ok is expected, why
    assert why == "campaign_marker_persisted"
