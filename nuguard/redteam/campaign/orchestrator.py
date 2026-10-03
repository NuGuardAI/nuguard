"""CampaignOrchestrator: ``redteam.mode: campaign``.

A subclass of :class:`RedteamOrchestrator`: all preparation (auth, endpoint
resolution, discovery, scenario generation, filtering, reporting, partial-run
salvage) is inherited unchanged. Only the *dispatch* phase differs — instead of
firing independent scenarios concurrently, :meth:`_run_scenarios` schedules
catalog objectives into conversation branches (breadth first, then depth),
defers retries to a cooldown queue instead of sleeping in a request slot, and
checkpoints campaign state after every objective.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger
from nuguard.common.transport import TransportOutcome, classify_transport
from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.models.finding import Finding
from nuguard.redteam.catalog.registry import SCENARIO_CATALOG
from nuguard.redteam.catalog.scheduling import (
    AvailableFixtures,
    SchedulingMeta,
    SessionPolicy,
    blocked_fixture_reason,
    scheduling_for,
)
from nuguard.redteam.executor.orchestrator import (
    RedteamOrchestrator,
    ScenarioRecord,
    _idor_chain_status_with_auth_caveat,
    _is_destructive_scenario,
    _is_direct_http_only_scenario,
    _maybe_mark_endpoint_not_found,
    _tally_transport,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.client import TargetUnavailableError

from . import projections
from .branches import BranchManager, ObjectiveRequirements
from .checkpoint import (
    DeferredObjective,
    build_payload,
    campaign_signature,
    restore,
    target_fingerprint,
)
from .config import CampaignConfig
from .confirmation import Candidate, ConfirmationRunner, ReproStatus, SetupItem
from .executor import CampaignExecutor
from .knowledge import KnowledgeStore, Scope
from .ledger import CoverageLedger, Status
from .llm_limiter import LLMLimiter
from .models import ObjectiveRun
from .planner import CampaignPlanner, plan_fingerprint
from .scheduler import BudgetTracker, CampaignScheduler, Objective, payload_fingerprint
from .transport import Principal, TargetLimiter
from .transport.capabilities import TargetCapabilities, classify_target
from .transport.health import CooldownQueue, HealthKey, HealthRegistry

if TYPE_CHECKING:
    from nuguard.redteam.executor.executor import AttackExecutor
    from nuguard.redteam.executor.guided_executor import GuidedAttackExecutor

_log = get_logger(__name__)

# Goal type -> preferred level for legacy (non-catalog) scenarios (spec §3 buckets).
_LEGACY_LEVEL: dict[GoalType, int] = {
    GoalType.RECON_INFERENCE: 1,
    GoalType.PROMPT_DRIVEN_THREAT: 3,
    GoalType.POLICY_VIOLATION: 3,
    GoalType.DATA_EXFILTRATION: 4,
    GoalType.API_ATTACK: 4,
    GoalType.PRIVILEGE_ESCALATION: 5,
    GoalType.TOOL_ABUSE: 5,
    GoalType.MCP_TOXIC_FLOW: 6,
    GoalType.AGENTIC_TRUST_ABUSE: 6,
}
_CROSS_ACCOUNT = frozenset({ScenarioType.CROSS_TENANT_EXFILTRATION, ScenarioType.IDOR})
_MAX_IDLE_SLEEP = 60.0


def _legacy_meta(scenario: AttackScenario, destructive: bool) -> SchedulingMeta:
    return SchedulingMeta(
        levels=(_LEGACY_LEVEL.get(scenario.goal_type, 5),),
        session_policy=SessionPolicy.ISOLATED if destructive else SessionPolicy.REUSE,
        technique_class=scenario.scenario_type.value,
        control_id=scenario.goal_type.value,
        resource_scope="api" if _is_direct_http_only_scenario(scenario) else "chat",
    )


class CampaignOrchestrator(RedteamOrchestrator):
    """Opt-in conversation-reuse engine (see ``documentation/developer-specs/redteam-v5.md``)."""

    _eager_enrichment = False

    def __init__(self, *, campaign: CampaignConfig | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._campaign_cfg = campaign or CampaignConfig()
        self.campaign_ledger = CoverageLedger()
        self.campaign_state: dict[str, Any] = {}
        self.campaign_coverage: Any = None
        self.campaign_objective_records: list[Any] = []
        self.campaign_reproduction_records: list[Any] = []
        self.campaign_branch_summaries: list[Any] = []
        self.campaign_observations: list[Any] = []
        self.campaign_plan: Any = None
        self.campaign_efficiency: Any = None
        self._campaign_runtime: dict[str, Any] = {}

    # -- checkpoint embedding -----------------------------------------------
    def _checkpoint_payload(self, **kw: Any) -> dict[str, Any]:
        payload = super()._checkpoint_payload(**kw)
        rt = self._campaign_runtime
        if rt:
            payload["campaign"] = build_payload(
                sbom=self._sbom, policy=self._effective_policy,
                target_fp=rt["target_fp"], auth_fps=rt["auth_fps"],
                fixture_version=self._campaign_cfg.fixture_version,
                ledger=self.campaign_ledger, branches=rt["branches"], store=rt["store"],
                budget=rt["budget"], completed=rt["completed"], deferred=rt["deferred"],
                pending_reproductions=rt["pending_repro"], write_objectives=rt["writes"],
                plan_cache=rt["planner"].plan_cache,
            )
        return payload

    # -- helpers --------------------------------------------------------------
    def _principal(self) -> Principal:
        headers = dict(getattr(self._target_session_config, "effective_headers", {}) or {})
        return Principal.from_headers("primary", headers) if headers else Principal.anonymous()

    def _fixtures(self, executor: "AttackExecutor") -> AvailableFixtures:
        tenants = getattr(self._canary_config, "tenants", None) or []
        return AvailableFixtures(
            second_principal=any(getattr(t, "session_token", "") for t in tenants),
            callback_server=getattr(executor, "_callback_canary", None) is not None,
            declared_fixture=self._campaign_cfg.declared_fixtures,
        )

    def _objective(self, scenario: AttackScenario, idx: int, principal: Principal) -> Objective:
        catalog = {s.id: s for s in (self._catalog or SCENARIO_CATALOG)}
        destructive = _is_destructive_scenario(scenario)
        spec = catalog.get(scenario.catalog_id) if scenario.catalog_id else None
        meta = scheduling_for(spec) if spec is not None else _legacy_meta(scenario, destructive)
        chain = scenario.chain
        first = ""
        if chain is not None and chain.steps:
            s0 = chain.steps[0]
            first = s0.payload or f"{s0.http_method} {s0.target_path}"
        boundary = "cross_account" if (
            scenario.identity_sensitive or scenario.scenario_type in _CROSS_ACCOUNT
        ) else "own_account"
        return Objective(
            objective_id=f"{scenario.catalog_id or 'legacy'}:{idx}",
            catalog_id=scenario.catalog_id or f"legacy:{scenario.scenario_type.value}",
            scenario=scenario, meta=meta,
            req=ObjectiveRequirements(
                principal.ref, meta.session_policy, meta.contamination_tags,
                persistent_write=destructive,
            ),
            identity_boundary=boundary,
            target=",".join(sorted(scenario.target_node_ids)),
            est_requests=len(chain.steps) if chain else (
                scenario.guided_conversation.max_turns if scenario.guided_conversation else 3),
            impact=scenario.impact_score,
            payload_fingerprint=payload_fingerprint(first) if first else "",
        )

    @staticmethod
    def _sig(o: Objective, principal: Principal) -> str:
        """Resume identity: catalog/type/target/principal plus the payload variant."""
        return f"{campaign_signature(o.scenario, principal.ref)}|v:{o.payload_fingerprint}"

    def _record(
        self, scenario: AttackScenario, status: str, had_finding: bool = False, **kw: Any
    ) -> ScenarioRecord:
        return ScenarioRecord(
            title=scenario.title, goal_type=scenario.goal_type.value,
            scenario_type=scenario.scenario_type.value, description=scenario.description,
            impact_score=scenario.impact_score,
            affected=", ".join(self._node_name.get(n, n) for n in scenario.target_node_ids[:2]),
            catalog_id=scenario.catalog_id or None, chain_status=status,
            had_finding=had_finding, **kw,
        )

    # -- dispatch ---------------------------------------------------------------
    async def _run_scenarios(  # type: ignore[override]  # noqa: C901
        self,
        scenarios: list[AttackScenario],
        executor: "AttackExecutor",
        guided_executor: "GuidedAttackExecutor | None" = None,
        progress_offset: int = 0,
        progress_total: int | None = None,
    ) -> tuple[list[Finding], list[tuple[str, str, bool]], list[ScenarioRecord]]:
        cfg = self._campaign_cfg
        client: Any = executor.client
        if type(client).__name__ == "WebSocketTargetClient":
            raise ValueError(
                "redteam.mode 'campaign' does not support WebSocket targets yet "
                "(single persistent socket, no parallel branches); use mode: concurrent."
            )
        caps: TargetCapabilities = classify_target(client).with_operator_reset_claim(
            cfg.target_supports_session_reset
        )
        principal = self._principal()
        base = str(self._target_url)
        scope = Scope(base, self._chat_path, principal.ref, principal.auth_scope, cfg.fixture_version)
        store = KnowledgeStore()
        sdisc = getattr(self._sbom, "discovered_profile", None)
        if sdisc:
            from nuguard.common.discovery import DiscoveredProfile

            store.seed_from_profile(DiscoveredProfile.model_validate(sdisc), scope)

        branches = BranchManager(
            cfg, lambda bid: client.new_session(bid) if hasattr(client, "new_session") else None  # type: ignore[arg-type,return-value]
        )
        budget = BudgetTracker(
            cfg.max_run_target_requests, cfg.max_run_seconds, cfg.max_run_llm_cost_usd,
            cfg.confirmation_reserve_fraction,
        )
        ledger = self.campaign_ledger
        sched = CampaignScheduler(ledger, budget)
        enricher = None
        llm_limiter: LLMLimiter | None = None
        if self._redteam_llm is not None:
            from nuguard.redteam.llm_engine.prompt_generator import LLMPromptGenerator

            llm_limiter = LLMLimiter(self._redteam_llm)
            enricher = LLMPromptGenerator(
                llm_limiter, self._sbom, self._effective_policy  # type: ignore[arg-type]
            )
        planner = CampaignPlanner(enricher, plan_fingerprint(
            evidence={"profile": bool(sdisc)}, config={"variants": 3}
        ))
        health = HealthRegistry(clock=time.monotonic, max_cooldown_seconds=cfg.retry_window_seconds)
        cooldown: CooldownQueue[tuple[Objective, int]] = CooldownQueue()
        cex = CampaignExecutor(
            client, branches, limiter=TargetLimiter(cfg.max_concurrent_requests),
            store=store, executor_kwargs=self._campaign_executor_kwargs(executor),
        )
        completed: set[str] = set()
        deferred: list[DeferredObjective] = []
        fixtures = self._fixtures(executor)
        rt: dict[str, Any] = {
            "target_fp": target_fingerprint(base, self._chat_path),
            "auth_fps": {principal.ref: principal.auth_scope}, "branches": branches, "store": store,
            "budget": budget, "completed": completed, "deferred": deferred,
            "pending_repro": [], "writes": set(), "planner": planner,
        }
        self._campaign_runtime = rt

        # Resume: restore campaign state (the legacy resume already filtered completed scenarios).
        saved = (self._resume_checkpoint or {}).get("campaign")
        if saved:
            rs = await restore(
                saved, sbom=self._sbom, policy=self._effective_policy,
                target_fp=rt["target_fp"], auth_fps=rt["auth_fps"],
                fixture_version=cfg.fixture_version, ledger=ledger, branches=branches,
                store=store, budget=budget, principals={principal.ref: principal},
                liveness=self._branch_alive(cex, scope),
            )
            completed |= rs.completed_signatures
            planner.plan_cache.update(rs.plan_cache)

        objectives = [self._objective(s, i, principal) for i, s in enumerate(scenarios)]
        live = sched.register(objectives)
        findings: list[Finding] = []
        executed: list[tuple[str, str, bool]] = []
        records: list[ScenarioRecord] = []
        t_start = time.monotonic()
        breadth_ids: set[str] = set()
        breadth_done = 0
        first_finding_t: float | None = None
        breadth_t: float | None = None
        delay_total = 0.0
        runs: list[ObjectiveRun] = []
        cand_map: list[tuple[Objective, list[Finding], ObjectiveRun]] = []

        def _finish(o: Objective, rec: ScenarioRecord, new: list[Finding], *, informative: bool) -> None:
            nonlocal first_finding_t, breadth_t, breadth_done
            records.append(rec)
            executed.append((o.scenario.title, o.scenario.goal_type.value, bool(new)))
            findings.extend(new)
            if new and first_finding_t is None:
                first_finding_t = time.monotonic() - t_start
            if o.objective_id in breadth_ids:
                breadth_done += 1
                if breadth_done == len(breadth_ids):
                    breadth_t = time.monotonic() - t_start
            completed.add(self._sig(o, principal))
            if o.req.persistent_write:
                rt["writes"].add(o.catalog_id)
            sched.record_outcome(o, informative=informative)
            self._save_checkpoint(status="in_progress", records=self.scenario_records + records,
                                  findings=self.findings + findings)

        # Ledger-only outcomes: redundant, blocked by missing fixtures, already completed.
        for o in objectives:
            e = ledger.entries[o.objective_id]
            if e.status == Status.REDUNDANT:
                records.append(self._record(o.scenario, f"redundant:{e.ref}"))
        runnable: list[Objective] = []
        for o in live:
            reason = blocked_fixture_reason(o.meta, fixtures)
            if reason:
                ledger.set_status(o.objective_id, Status.BLOCKED, reason=reason)
                records.append(self._record(o.scenario, reason))
            elif self._sig(o, principal) in completed:
                ledger.set_status(o.objective_id, Status.COMPLETED)
            else:
                runnable.append(o)

        plan = sched.plan_breadth(runnable)
        for o in plan.deferred:
            records.append(self._record(o.scenario, "budget_deferred"))
        if not plan.fits:
            self.config_notes.append(
                f"campaign: {len(plan.deferred)} objective(s) do not fit the finite request "
                "budget and were budget_deferred before starting (see coverage)."
            )
        pending = list(plan.selected)
        pending += sched.next_depth(
            [o for o in runnable if o not in plan.selected and o not in plan.deferred]
        )
        consecutive_unavailable = 0
        breadth_ids.update(o.objective_id for o in plan.selected)

        while pending or len(cooldown):
            for o, step in cooldown.pop_ready(time.monotonic()):
                pending.insert(0, o)
                o.scenario.__dict__.setdefault("_campaign_resume", step)
            if not pending:
                nxt = cooldown.next_eligible_at()
                if nxt is None:
                    break
                wait = min(max(0.0, nxt - time.monotonic()), _MAX_IDLE_SLEEP)
                delay_total += wait
                await asyncio.sleep(wait)
                continue
            if budget.exhausted():
                for o in pending:
                    ledger.set_status(o.objective_id, Status.BUDGET_DEFERRED, reason="run_budget_exhausted")
                    records.append(self._record(o.scenario, "budget_deferred"))
                pending.clear()
                break
            o = pending.pop(0)
            key = HealthKey(base, self._chat_path, "POST", principal.ref, "chat")
            if health.is_paused(key):
                cooldown.push((o, o.scenario.__dict__.get("_campaign_resume", 0)), health.resume_at(key))
                continue
            try:
                new_f, rec, informative, defer, run = await self._run_objective(
                    o, cex, principal, scope, guided_executor, planner
                )
            except TargetUnavailableError:
                consecutive_unavailable += 1
                d = health.record(key, TransportOutcome.REQUEST_ERROR, now=time.monotonic())
                records.append(self._record(o.scenario, "aborted:target_unavailable"))
                if consecutive_unavailable >= 3 or health.all_paused():
                    self._circuit_open = True
                    self.config_notes.append(
                        "campaign: target unavailable — checkpointed and stopped "
                        f"(health action: {d.action.value})."
                    )
                    self._save_checkpoint(status="in_progress", records=self.scenario_records + records,
                                          findings=self.findings + findings)
                    break
                continue
            consecutive_unavailable = 0
            if defer is not None:
                delay, step = defer
                health.record(key, TransportOutcome.RATE_LIMIT, retry_after=delay, now=time.monotonic())
                cooldown.push((o, step), time.monotonic() + delay)
                deferred.append(DeferredObjective(
                    self._sig(o, principal), time.monotonic() + delay, step, "retry_deferred"))
                continue
            budget.spend(requests=rec.turns_used)
            health.record(key, TransportOutcome.OK, now=time.monotonic())
            runs.append(run)
            if new_f:
                cand_map.append((o, new_f, run))
            _finish(o, rec, new_f, informative=informative)

        repros = []
        if cfg.confirm_in_fresh_sessions and cand_map and not self._circuit_open:
            repros = await self._confirm_findings(
                cand_map, cex, caps, principal, scope, budget, runs
            )
        cex.branches.complete_campaign()
        requests_total = budget.requests_used + sum(r.trials for r in repros)
        self.campaign_coverage = projections.coverage_summary(ledger, len(findings))
        self.campaign_objective_records = projections.objective_records(runs)
        self.campaign_reproduction_records = [projections.reproduction_public(r) for r in repros]
        self.campaign_branch_summaries = projections.branch_summaries(branches)
        self.campaign_observations = projections.observations(store)
        self.campaign_plan = projections.plan_summary(
            planner.group(runnable), plan.selected, plan.deferred, plan.fits
        )
        self.campaign_efficiency = projections.efficiency_summary(
            runs=runs, manager=branches, requests=requests_total, llm=llm_limiter,
            retry_delay_seconds=delay_total, time_to_first_finding_s=first_finding_t,
            time_to_representative_coverage_s=breadth_t,
        )
        self.campaign_state = {
            "ledger": ledger.counts(),
            "branches": len(branches.branches),
            "capabilities": asdict(caps) if hasattr(caps, "__dataclass_fields__") else {},
            "unresolved": [e.objective_id for e in ledger.unresolved()],
        }
        return findings, executed, records

    # -- single objective ---------------------------------------------------------
    def _campaign_executor_kwargs(self, executor: "AttackExecutor") -> dict[str, Any]:
        """Reuse the legacy executor's evaluators/canary/policy for branch executors."""
        ev = executor
        return {
            "policy": getattr(getattr(ev, "_evaluator", None), "_policy", None),
            "canary": ev._canary, "logger": ev._logger,
            "eval_llm": getattr(getattr(ev, "_response_evaluator", None), "_llm", None),
            "mutation_llm": getattr(getattr(ev, "_adaptive_mutator", None), "_llm", None),
            "app_log_reader": ev._app_log_reader, "auth_session": ev._auth_session,
            "app_domain": ev._app_domain, "allowed_topics": ev._allowed_topics,
            "sbom": ev._sbom, "suppress_spa_html_auth_bypass": ev._suppress_spa_html,
            "credentials": ev._credentials or None, "callback_canary": ev._callback_canary,
        }

    def _branch_alive(self, cex: CampaignExecutor, scope: Scope) -> Any:
        async def alive(branch: Any) -> bool:
            if not branch.transport.session_context and cex.branches.needs_baseline(branch):
                return True
            try:
                reply, _ = await cex.branch_client(branch).send(
                    "Hi again — are you still there?", branch.session
                )
            except Exception:  # noqa: BLE001
                return False
            return not reply.startswith(("[HTTP ", "[REQUEST_ERROR:"))

        return alive

    async def _run_objective(
        self,
        o: Objective,
        cex: CampaignExecutor,
        principal: Principal,
        scope: Scope,
        guided_executor: "GuidedAttackExecutor | None",
        planner: CampaignPlanner,
    ) -> tuple[list[Finding], ScenarioRecord, bool, tuple[float, int] | None, ObjectiveRun]:
        """Run one objective; returns (findings, record, informative, defer, run)."""
        scenario = o.scenario
        t0 = time.perf_counter()
        branch = cex.branches.acquire(principal, o.req)
        if self._campaign_cfg.campaign_warmup and cex.branches.needs_baseline(branch):
            await cex.ensure_baseline(branch, scope)
        sess = branch.session
        pf = getattr(self._sbom, "discovered_profile", None)
        if pf and not sess.golden_ids and not sess.golden_data:
            sess.golden_data = str(pf.get("raw_response", ""))
            sess.golden_ids = list(pf.get("ids", []))
            sess.golden_name = str(pf.get("customer_name", ""))

        if scenario.guided_conversation is not None and guided_executor is not None:
            from nuguard.redteam.executor.guided_executor import GuidedAttackExecutor as _GE

            bc = cex.branch_client(branch)
            ge = _GE(
                client=bc, director=guided_executor._director, logger=guided_executor._logger,  # type: ignore[arg-type]
                canary=guided_executor._canary, app_log_reader=guided_executor._app_log_reader,
                credentials=guided_executor._credentials or None, sbom=guided_executor._sbom,
                tree_breadth=guided_executor._tree_breadth, tree_max_depth=guided_executor._tree_max_depth,
                evaluator=guided_executor._evaluator,
                hard_refusal_abort_turns=guided_executor._hard_refusal_abort_turns,
                auth_session=guided_executor._auth_session,
            )
            cex.branches.begin(branch, o.catalog_id)
            turn0 = len(sess.turns)
            from nuguard.redteam.campaign.transport import RetryDeferred

            try:
                new_f, _tup, rec = await self._run_guided_scenario(
                    scenario, ge, rec_affected(self, scenario), session=sess,
                    setup_done=branch.baseline_done,
                )
            except RetryDeferred as rd:
                branch.active_objective = None
                return (
                    [], self._record(scenario, "deferred"), False, (rd.delay_seconds, 0),
                    self._blank_run(o, branch, turn0),
                )
            rec.duration_s = time.perf_counter() - t0
            rec.turns_used = len(sess.turns) - turn0
            rec.turns_budget = scenario.guided_conversation.max_turns
            cex.branches.release(
                branch, o.catalog_id, o.req, attack_turns=rec.turns_used,
                attack_accepted=rec.had_finding,
            )
            run = self._blank_run(o, branch, turn0)
            run.turn_end = len(sess.turns)
            run.attack_accepted = rec.had_finding
            self._stamp_findings(new_f, o)
            return new_f, rec, rec.had_finding, None, run

        if scenario.chain is None:
            return [], self._record(scenario, "failed"), False, None, self._blank_run(o, branch, 0)
        jit = await planner.enrich_ready([o])
        if jit:
            from nuguard.redteam.llm_engine.prompt_generator import _inject_llm_payloads

            o.scenario = scenario = _inject_llm_payloads([scenario], jit)[0]
        resume = int(scenario.__dict__.pop("_campaign_resume", 0))
        scenario.chain.decorator_allowed = scenario.decorator_allowed
        orec = await cex.run_static(scenario, branch, o.req, resume_step_index=resume)
        if orec.status == "deferred":
            return (
                [], self._record(scenario, "deferred"), False,
                (orec.defer_seconds, orec.resume_step_index), orec,
            )
        chain = scenario.chain
        chain.status = "aborted" if orec.status in ("aborted", "effect_unknown") else "completed"
        step_results = orec.step_results
        details = self._build_step_details(step_results)
        new_f = self._build_findings(scenario, chain, step_results, details, session=sess)
        had = bool(new_f)
        status = _idor_chain_status_with_auth_caveat(
            scenario, had, self._no_target_auth,
            "effect_unknown" if orec.status == "effect_unknown" else chain.status,
        )
        rec = self._record(
            scenario, status, had, steps=details, duration_s=time.perf_counter() - t0,
            turns_used=sum(1 for d in details if d.get("step_type") != "WARMUP"),
            turns_budget=len(chain.steps),
        )
        _tally_transport(rec, step_results)
        rec.chain_status = _maybe_mark_endpoint_not_found(
            rec, rec.chain_status, _is_direct_http_only_scenario(scenario)
        )
        informative = had or any(
            classify_transport(r.response) == TransportOutcome.OK for r in step_results
        )
        self._stamp_findings(new_f, o)
        return new_f, rec, informative, None, orec

    @staticmethod
    def _guided_candidate_scenario(scenario: AttackScenario) -> AttackScenario | None:
        """Static replay form of a *successful* guided conversation (its attacker turns, in order).

        Guided findings carry no fixed payloads; the attacker messages that were actually
        sent are replayed verbatim as INJECT steps in a fresh branch so history-dependent
        breakthroughs (rapport, bridge, escalate) are reproduced, not just the last message.
        """
        from nuguard.models.exploit_chain import ExploitChain, ExploitStep

        conv = scenario.guided_conversation
        if conv is None or not conv.succeeded or not conv.turns:
            return None
        steps = [
            ExploitStep(
                step_id=f"{conv.conversation_id}_r{t.turn}", step_type="INJECT",
                description=f"replay of guided turn {t.turn} [{t.tactic_used}]",
                payload=t.attacker_message, on_failure="skip",
                depends_on=[f"{conv.conversation_id}_r{conv.turns[i - 1].turn}"] if i else [],
            )
            for i, t in enumerate(conv.turns)
        ]
        chain = ExploitChain(
            chain_id=f"{conv.conversation_id}-replay", goal_type=conv.goal_type,
            scenario_type=scenario.scenario_type, sbom_path=list(conv.sbom_path), steps=steps,
        )
        return scenario.model_copy(update={"chain": chain, "guided_conversation": None})

    def _guided_judge(self, scenario: AttackScenario) -> Any:
        """Judge a replay with the same criteria the live conversation used (score == SUCCESS_SCORE)."""
        conv = scenario.guided_conversation
        llm = self._redteam_llm
        if conv is None or llm is None:
            return None
        from nuguard.redteam.llm_engine.conversation_director import ConversationDirector

        director = ConversationDirector(
            llm=llm, eval_llm=self._eval_llm or llm,  # type: ignore[arg-type]
            goal_type=conv.goal_type, goal_description=conv.goal_description,
            max_turns=conv.max_turns, mutation_mode=self._guided_mutation_mode,
        )
        milestone = conv.milestones[-1] if conv.milestones else conv.goal_description

        async def judge(record: Any) -> bool:
            last = record.step_results[-1]
            tactic = conv.turns[-1].tactic_used if conv.turns else ""
            score, *_ = await director.assess_progress(
                last.resolved_payload, last.response, milestone, tactic
            )
            return score >= director.SUCCESS_SCORE

        return judge

    @staticmethod
    def _blank_run(o: Objective, branch: Any, turn_start: int) -> ObjectiveRun:
        return ObjectiveRun(
            catalog_id=o.scenario.catalog_id, scenario_id=o.scenario.scenario_id,
            branch_id=branch.branch_id, turn_start=turn_start,
            ancestor_setup_refs=tuple(branch.objective_history),
        )

    @staticmethod
    def _stamp_findings(findings: list[Finding], o: Objective) -> None:
        """Campaign evidence fields every finding carries (versions are never relabelled)."""
        for f in findings:
            f.catalog_id = o.scenario.catalog_id or None
            f.framework_versions = list(projections.FRAMEWORK_VERSIONS)
            f.reproduction_status = f.reproduction_status or ReproStatus.NOT_ATTEMPTED.value

    # -- fresh-session confirmation ---------------------------------------------
    async def _confirm_findings(
        self,
        cand_map: list[tuple[Objective, list[Finding], ObjectiveRun]],
        cex: CampaignExecutor,
        caps: TargetCapabilities,
        principal: Principal,
        scope: Scope,
        budget: BudgetTracker,
        runs: list[ObjectiveRun],
    ) -> list[Any]:
        """Reproduce each candidate in a brand-new conversation using reserved capacity."""
        runner = ConfirmationRunner(
            cex, caps, self._campaign_cfg, budget,
            detect=lambda r: bool(r.success_signal_found or r.canary_hits or r.policy_violations),
        )
        by_catalog = {r.catalog_id: r for r in runs}
        out: list[Any] = []
        for o, fs, run in cand_map:
            guided = self._guided_candidate_scenario(o.scenario)
            if o.scenario.chain is None and guided is None:
                continue
            deterministic = any(r.canary_hits for r in run.step_results)
            setup = []
            for ref in o.meta.prerequisites:
                prior = by_catalog.get(ref)
                if prior is None or ref.startswith("L") or prior.branch_id != run.branch_id:
                    continue
                steps = tuple(sr.step for sr in prior.step_results)
                setup.append(SetupItem(ref, steps, write=ref in self._campaign_runtime["writes"]))
            cand = Candidate(
                o.objective_id, guided or o.scenario, principal, o.req, scope, setup=setup,
                evidence_kind="canary" if deterministic else "response_quote",
                deterministic=deterministic,
                original_evidence=(fs[0].evidence_quote or fs[0].evidence or "")[:500],
                judge_only=guided is not None,
                judge=self._guided_judge(o.scenario) if guided is not None else None,
            )
            rec = await runner.confirm(cand)
            out.append(rec)
            for f in fs:
                f.reproduction_status = rec.status.value
                f.evidence_kind = rec.evidence_kind
                f.effect_verified = rec.effect_verified
                f.verified = (
                    True if rec.status == ReproStatus.CONFIRMED
                    else False if rec.status == ReproStatus.NOT_REPRODUCED else None
                )
                if rec.status == ReproStatus.NOT_REPRODUCED:
                    f.reasoning = (
                        f"{f.reasoning} [Not reproduced in a fresh session: "
                        "context-dependent; original evidence retained.]"
                    ).strip()
            self._campaign_runtime["pending_repro"].append(
                {"objective": o.objective_id, "status": rec.status.value}
            )
        return out


def rec_affected(orch: CampaignOrchestrator, scenario: AttackScenario) -> str:
    return ", ".join(orch._node_name.get(n, n) for n in scenario.target_node_ids[:2])
