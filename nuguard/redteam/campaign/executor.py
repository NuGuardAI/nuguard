"""CampaignExecutor: runs objectives on conversation branches, one turn at a time.

It reuses the legacy per-step pipeline (:meth:`AttackExecutor.run_step`) and the
guided executor rather than the legacy ``run`` loops, because those own session
creation, DISCOVER/warm-up injection and sleeping retries — all of which the
campaign scheduler now owns:

* no per-chain session, DISCOVER step or happy-path warm-up (one baseline per
  branch, via :func:`~nuguard.redteam.campaign.discovery.run_clean_baseline`);
* no executor-level sleeps — a retriable condition raises ``RetryDeferred`` and
  the objective is returned as ``deferred`` for the cooldown queue;
* one turn at a time per branch (callers must not run two objectives on one
  branch concurrently).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from nuguard.common.logging import get_logger
from nuguard.models.exploit_chain import ExploitStep
from nuguard.redteam.executor.chain_assembler import ChainAssembler
from nuguard.redteam.executor.executor import AttackExecutor, StepResult
from nuguard.redteam.scenarios.scenario_types import AttackScenario

from .branches import Branch, BranchManager, ObjectiveRequirements
from .discovery import run_clean_baseline
from .knowledge import KnowledgeStore, Scope
from .models import ObjectiveExecutionRecord
from .transport import BranchSender, RetryDeferred, TargetLimiter

if TYPE_CHECKING:
    from nuguard.redteam.executor.guided_executor import GuidedAttackExecutor
    from nuguard.redteam.llm_engine.conversation_director import ConversationDirector
    from nuguard.redteam.target.client import TargetAppClient

_log = get_logger(__name__)

_SETUP_STEP_TYPES = ("DISCOVER", "WARMUP")


class BranchClient:
    """Duck-typed ``TargetClient`` bound to one branch.

    The legacy executors take a ``client`` and call ``send`` / ``invoke_endpoint``
    / ``update_default_headers`` on it. Handing them a :class:`BranchClient`
    routes every request through the branch's transport (session ids, cookies,
    headers, deferred retries) without changing either executor. Attributes not
    overridden here fall through to the shared client (read-mostly helpers such
    as ``base_url``).
    """

    def __init__(self, sender: BranchSender, client: "TargetAppClient") -> None:
        self._sender = sender
        self._client = client

    async def send(
        self,
        payload: str,
        session: Any,
        extra_headers: dict[str, str] | None = None,
        retry_transient: bool = False,  # noqa: ARG002 — retries are the scheduler's job
    ) -> tuple[str, list[dict]]:
        return await self._sender.send(payload, session, extra_headers)

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        return await self._sender.invoke(
            path, method, body, params, extra_headers, strip_auth
        )

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        """Auth refresh lands on the branch, never on the shared client defaults."""
        if headers:
            self._sender.transport.headers.update(headers)

    def new_session(self, chain_id: str) -> Any:
        return self._client.new_session(chain_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class CampaignExecutor:
    """Executes static and guided objectives on branches."""

    def __init__(
        self,
        client: "TargetAppClient",
        branches: BranchManager,
        *,
        limiter: TargetLimiter | None = None,
        executor_kwargs: dict[str, Any] | None = None,
        store: KnowledgeStore | None = None,
        scope_for: Callable[[Branch], Scope] | None = None,
    ) -> None:
        self._client = client
        self.branches = branches
        self._limiter = limiter or TargetLimiter()
        self._executor_kwargs = dict(executor_kwargs or {})
        self.store = store
        self._scope_for = scope_for
        self._executors: dict[str, AttackExecutor] = {}

    # -- plumbing -----------------------------------------------------------
    def branch_client(self, branch: Branch) -> BranchClient:
        sender = BranchSender(self._client, branch.transport, self._limiter)
        return BranchClient(sender, self._client)

    def _executor(self, branch: Branch) -> AttackExecutor:
        ex = self._executors.get(branch.branch_id)
        if ex is None:
            kwargs = {"turn_delay_seconds": 0.0, **self._executor_kwargs}
            ex = AttackExecutor(client=self.branch_client(branch), **kwargs)  # type: ignore[arg-type]
            self._executors[branch.branch_id] = ex
        return ex

    # -- baseline -----------------------------------------------------------
    async def ensure_baseline(
        self, branch: Branch, scope: Scope, *, message: str | None = None
    ) -> bool:
        """Send the one benign warm-up for *branch* (idempotent per branch)."""
        if not self.branches.needs_baseline(branch):
            return True
        if self.store is None:
            raise ValueError("ensure_baseline requires a KnowledgeStore")
        bc = self.branch_client(branch)

        async def send(text: str) -> str:
            reply, calls = await bc.send(text, branch.session)
            # The baseline is real conversation context: client-history targets
            # replay it, so it must live in the branch session like any turn.
            branch.session.add_turn(text, reply, calls)
            return reply

        kwargs: dict[str, Any] = {} if message is None else {"message": message}
        ok = await run_clean_baseline(send, self.store, scope, **kwargs)
        if ok:
            self.branches.mark_baseline_done(branch)
        return ok

    # -- static objectives ----------------------------------------------------
    async def run_static(
        self,
        scenario: AttackScenario,
        branch: Branch,
        req: ObjectiveRequirements,
        *,
        resume_step_index: int = 0,
    ) -> ObjectiveExecutionRecord:
        """Run *scenario*'s chain on *branch*; never sleeps, never sets up a session."""
        chain = scenario.chain
        if chain is None:
            raise ValueError(f"scenario {scenario.scenario_id} has no static chain")
        ref = scenario.catalog_id or scenario.scenario_id
        session = branch.session
        rec = ObjectiveExecutionRecord(
            catalog_id=scenario.catalog_id,
            scenario_id=scenario.scenario_id,
            branch_id=branch.branch_id,
            turn_start=len(session.turns),
            ancestor_setup_refs=tuple(branch.objective_history),
        )
        self.branches.begin(branch, ref)
        executor = self._executor(branch)
        steps = [
            s
            for s in ChainAssembler.sort_steps(chain)
            if s.step_type not in _SETUP_STEP_TYPES
            and not (s.step_type in ("SCAN", "EVALUATE", "OBSERVE") and not s.payload)
        ]
        for idx, step in enumerate(steps):
            if idx < resume_step_index:
                continue
            try:
                result = await executor.run_step(step, session, chain)
            except RetryDeferred as rd:
                rec.status = "deferred"
                rec.resume_step_index = idx
                rec.defer_seconds = rd.delay_seconds
                rec.defer_reason = rd.reason
                break
            rec.step_results.append(result)
            rec.tool_traces.append(list(result.tool_calls))
            if result.response.startswith("[REQUEST_ERROR:") and _is_write(step):
                rec.status = "effect_unknown"
                rec.unknown_state = True
                break
            if result.success_signal_found:
                rec.attack_accepted = True
                session.add_evidence(step.step_id, result.response)
                if step.abort_chain_on_success:
                    break
            elif step.on_failure == "abort":
                rec.status = "aborted"
                break
        rec.turn_end = len(session.turns)
        self._finish(branch, ref, req, rec)
        return rec

    # -- guided objectives ----------------------------------------------------
    async def run_guided(
        self,
        scenario: AttackScenario,
        branch: Branch,
        req: ObjectiveRequirements,
        director_factory: Callable[[bool], "ConversationDirector"],
        executor_factory: Callable[[BranchClient, "ConversationDirector"], "GuidedAttackExecutor"],
    ) -> ObjectiveExecutionRecord:
        """Run a guided conversation on *branch* with its own director.

        ``director_factory(setup_done)`` must return a *new* director per objective
        (no shared director state across objectives). On a warm branch
        (``setup_done=True``) the director skips happy-path/rapport openers.
        """
        conv = scenario.guided_conversation
        if conv is None:
            raise ValueError(f"scenario {scenario.scenario_id} has no guided conversation")
        ref = scenario.catalog_id or scenario.scenario_id
        rec = ObjectiveExecutionRecord(
            catalog_id=scenario.catalog_id,
            scenario_id=scenario.scenario_id,
            branch_id=branch.branch_id,
            turn_start=len(branch.session.turns),
            ancestor_setup_refs=tuple(branch.objective_history),
        )
        self.branches.begin(branch, ref)
        director = director_factory(branch.baseline_done)
        guided = executor_factory(self.branch_client(branch), director)
        try:
            await guided.run(conv, branch.session)
        except RetryDeferred as rd:
            rec.status = "deferred"
            rec.defer_seconds = rd.delay_seconds
            rec.defer_reason = rd.reason
        else:
            rec.attack_accepted = bool(getattr(conv, "succeeded", False))
        rec.turn_end = len(branch.session.turns)
        self._finish(branch, ref, req, rec)
        return rec

    # -- shared -------------------------------------------------------------
    def _finish(
        self, branch: Branch, ref: str, req: ObjectiveRequirements, rec: ObjectiveExecutionRecord
    ) -> None:
        if rec.status == "deferred":
            branch.active_objective = None  # branch stays; the objective re-queues
            return
        reason = self.branches.release(
            branch,
            ref,
            req,
            attack_turns=rec.turns,
            attack_accepted=rec.attack_accepted,
            unknown_state=rec.unknown_state,
        )
        if reason is not None:
            _log.info("branch %s retired after %s: %s", branch.branch_id, ref, reason.value)
            self._executors.pop(branch.branch_id, None)


def _is_write(step: ExploitStep) -> bool:
    return bool(step.target_path) and step.http_method.upper() in ("POST", "PUT", "PATCH", "DELETE")


__all__ = ["BranchClient", "CampaignExecutor", "StepResult"]

