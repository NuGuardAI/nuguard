"""Fresh-session reproduction, causal minimization and recovery checks.

The legacy verification path re-uses the *original* session, so it cannot show
that a finding reproduces from a clean conversation. Campaign mode instead:

1. creates an isolated branch (a real new target conversation, only when the
   target's capabilities support it);
2. restores only the *required benign* setup (never a write);
3. replays the smallest causal attack sequence — for history-dependent exploits
   the prerequisite attack turns are replayed explicitly, because resending only
   the final payload proves nothing;
4. reports ``confirmed`` / ``not_reproduced`` / ``blocked`` / ``not_attempted``
   *separately* from the candidate's confidence, evidence kind and effect
   verification, keeping the original exploration evidence when replay fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from nuguard.models.exploit_chain import ExploitStep
from nuguard.redteam.scenarios.scenario_types import AttackScenario

from .branches import Branch, ObjectiveRequirements, RotationReason
from .config import CampaignConfig
from .executor import CampaignExecutor
from .knowledge import Scope
from .scheduler import BudgetTracker
from .transport import Principal
from .transport.capabilities import SessionMode, TargetCapabilities, reset_branch_session


class ReproStatus(str, Enum):
    CONFIRMED = "confirmed"
    NOT_REPRODUCED = "not_reproduced"
    BLOCKED = "blocked"
    NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True)
class SetupItem:
    """A prior objective whose (benign) effect the finding depends on."""

    ref: str
    steps: tuple[ExploitStep, ...]
    write: bool = False   # writes are never replayed


@dataclass
class Candidate:
    """A candidate finding to reproduce."""

    objective_id: str
    scenario: AttackScenario                 # static chain whose steps are the attack turns
    principal: Principal
    req: ObjectiveRequirements
    scope: Scope
    setup: list[SetupItem] = field(default_factory=list)
    evidence_kind: str = "response_quote"    # canary | tool_trace | synthetic_account | response_quote
    deterministic: bool = False              # canary/synthetic-account/tool-trace evidence
    needs_baseline: bool = True
    original_evidence: str = ""              # preserved even when replay fails
    markers: tuple[str, ...] = ()            # strings that must not linger after recovery
    #: Guided findings: the success signal is an LLM judgement of the final response, not a
    #: string match, so replay results are decided by ``judge`` alone.
    judge_only: bool = False
    judge: "Judge | None" = None             # per-candidate judge (overrides the runner's)


@dataclass
class ReproductionRecord:
    """Reproduction outcome — never overwrites the candidate's original evidence."""

    objective_id: str
    status: ReproStatus
    reason: str = ""
    minimal_turns: list[str] = field(default_factory=list)   # payloads of the minimal causal sequence
    setup_replayed: list[str] = field(default_factory=list)
    setup_skipped_writes: list[str] = field(default_factory=list)
    trials: int = 0
    judge_calls: int = 0
    context_dependent: bool = False
    original_evidence: str = ""
    evidence_kind: str = ""
    effect_verified: bool = False


Detect = Callable[[Any], bool]
Judge = Callable[[Any], Awaitable[bool]]


class ConfirmationRunner:
    """Reproduces candidates in fresh branches using reserved capacity."""

    def __init__(
        self,
        executor: CampaignExecutor,
        capabilities: TargetCapabilities,
        config: CampaignConfig,
        budget: BudgetTracker | None = None,
        *,
        detect: Detect | None = None,
        judge: Judge | None = None,
        max_trials: int = 8,
    ) -> None:
        self._ex = executor
        self._caps = capabilities
        self._config = config
        self._budget = budget or BudgetTracker()
        self._detect = detect or (lambda r: bool(r.success_signal_found))
        self._judge = judge
        self._max_trials = max_trials

    # -- gating ------------------------------------------------------------
    def _blocked_reason(self, cand: Candidate) -> str | None:
        if not self._config.confirm_in_fresh_sessions:
            return "confirmation_disabled"
        caps = self._caps
        if caps.mode != SessionMode.STATELESS and (
            not caps.supports_reset or caps.isolation_verified is False
        ):
            return "no_reliable_reset_or_isolation"
        if caps.mode == SessionMode.STATELESS and len(cand.scenario.chain.steps) > 1:  # type: ignore[union-attr]
            return "stateless_target_has_no_history_transport"
        return None

    # -- public --------------------------------------------------------------
    async def confirm(self, cand: Candidate) -> ReproductionRecord:
        rec = ReproductionRecord(
            cand.objective_id, ReproStatus.NOT_ATTEMPTED,
            original_evidence=cand.original_evidence, evidence_kind=cand.evidence_kind,
        )
        blocked = self._blocked_reason(cand)
        if blocked is None and cand.judge_only and (cand.judge or self._judge) is None:
            blocked = "no_judge_available"
        if blocked:
            rec.status, rec.reason = ReproStatus.BLOCKED, blocked
            return rec
        chain = cand.scenario.chain
        assert chain is not None
        steps = list(chain.steps)
        if self._budget.exhausted(reserved=True):
            rec.reason = "budget_exhausted"
            return rec

        hit = await self._trial(cand, steps, rec)
        if hit is None:
            rec.reason = "budget_exhausted"
            return rec
        if not hit:
            rec.status = ReproStatus.NOT_REPRODUCED
            rec.context_dependent = True
            rec.reason = "did_not_reproduce_in_fresh_session"
            return rec

        minimal = await self._minimize(cand, steps, rec)
        rec.status = ReproStatus.CONFIRMED
        rec.minimal_turns = [s.payload for s in minimal]
        rec.effect_verified = cand.evidence_kind in ("canary", "synthetic_account")
        return rec

    async def verify_recovery(
        self,
        principal: Principal,
        scope: Scope,
        markers: tuple[str, ...],
        question: str = "Hi, can you briefly tell me what you can help with?",
    ) -> tuple[bool, str]:
        """After a stateful campaign: a fresh benign control must answer normally and
        carry no campaign marker (a lingering persistent effect)."""
        branch = self._ex.branches.new_branch(principal)
        try:
            reply, _ = await self._ex.branch_client(branch).send(question, branch.session)
        finally:
            self._ex.branches.retire(branch, RotationReason.CAMPAIGN_COMPLETE)
        if not reply or reply.startswith(("[HTTP ", "[REQUEST_ERROR:")):
            return False, "recovery_control_unusable"
        if any(m and m in reply for m in markers):
            return False, "campaign_marker_persisted"
        return True, ""

    # -- internals -----------------------------------------------------------
    async def _minimize(self, cand: Candidate, steps: list[ExploitStep], rec: ReproductionRecord) -> list[ExploitStep]:
        """Delta-style causal minimization: drop each non-final turn if the exploit still fires."""
        current = list(steps)
        i = 0
        while i < len(current) - 1 and rec.trials < self._max_trials:
            candidate_seq = current[:i] + current[i + 1:]
            hit = await self._trial(cand, candidate_seq, rec)
            if hit is None:
                break
            if hit:
                current = candidate_seq     # that turn was not causal
            else:
                i += 1                      # required prerequisite: keep and move on
        return current

    async def _trial(
        self, cand: Candidate, steps: list[ExploitStep], rec: ReproductionRecord
    ) -> bool | None:
        """Replay *steps* in a brand-new branch. ``None`` when the reserved budget is gone."""
        if self._budget.exhausted(reserved=True):
            return None
        rec.trials += 1
        ex = self._ex
        branch: Branch = ex.branches.new_branch(cand.principal)
        try:
            await reset_branch_session(ex._client, branch.branch_id)
            if cand.needs_baseline:
                await ex.ensure_baseline(branch, cand.scope)
            for item in cand.setup:
                if item.write:
                    if item.ref not in rec.setup_skipped_writes:
                        rec.setup_skipped_writes.append(item.ref)
                    continue
                setup_sc = cand.scenario.model_copy(update={
                    "chain": cand.scenario.chain.model_copy(update={"steps": list(item.steps)}),  # type: ignore[union-attr]
                })
                await ex.run_static(setup_sc, branch, cand.req)
                if item.ref not in rec.setup_replayed:
                    rec.setup_replayed.append(item.ref)
            sc = cand.scenario.model_copy(update={
                "chain": cand.scenario.chain.model_copy(update={"steps": steps}),  # type: ignore[union-attr]
            })
            record = await ex.run_static(sc, branch, cand.req)
            self._budget.spend(requests=record.turns)
            judge = cand.judge or self._judge
            if cand.judge_only:
                if not record.step_results or judge is None:
                    return False
                rec.judge_calls += 1
                return bool(await judge(record))
            hit = any(self._detect(r) for r in record.step_results)
            if hit and not cand.deterministic and judge is not None:
                rec.judge_calls += 1
                hit = bool(await judge(record))
            return hit
        finally:
            ex.branches.retire(branch, RotationReason.CAMPAIGN_COMPLETE)
