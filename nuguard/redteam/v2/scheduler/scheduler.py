"""Phased scheduler — runs objectives in attacker-like stages, safely.

Responsibilities (the design's Intelligent Test Ordering + safety rules):

* **Phase ordering** — objectives run in ascending :class:`Phase`; high-impact
  phases run only after their prerequisite phases have completed.
* **Early stop** — if a critical/uncontrolled side effect is observed, later
  high-impact phases are skipped (evidence preserved).
* **Resource locks** — objectives sharing a ``resource_locks`` key are serialized
  (acquired in sorted order to avoid lock-ordering cycles); independent objectives
  run concurrently under an :class:`asyncio.Semaphore`.
* **Fresh identity** — every objective gets a fresh identity/conversation, except
  state-reusing tests (memory/persistence) which share a stable identity so a
  write in one objective is visible to a later read.
* **Safety** — each objective is screened by :mod:`safety` before execution;
  reset hooks are surfaced after state-mutating objectives.

The scheduler is execution-agnostic: it drives a caller-supplied ``runner``
coroutine, so it can be unit-tested with a fake runner and reused by the Phase 5
executor unchanged.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from nuguard.common.logging import get_logger
from nuguard.redteam.v2.knowledge.schema import StateImpact
from nuguard.redteam.v2.planning.objective_generator import ScenarioObjective
from nuguard.redteam.v2.scheduler.phases import (
    HIGH_IMPACT_PHASES,
    Phase,
    phase_from_int,
)
from nuguard.redteam.v2.scheduler.safety import SafetyPolicy, assess

_log = get_logger(__name__)

# Families/state impacts that legitimately reuse session state across objectives.
_STATE_REUSING_STATES: frozenset[str] = frozenset({StateImpact.MEMORY_WRITE.value})
_STATE_REUSING_FAMILIES: frozenset[str] = frozenset({"memory_poisoning"})


@dataclass
class RunContext:
    """Per-objective execution context handed to the runner."""

    objective: ScenarioObjective
    phase: Phase
    identity: str
    fresh_identity: bool


@dataclass
class ScheduledResult:
    """Outcome of one scheduled objective."""

    objective: ScenarioObjective
    phase: Phase
    status: str  # "completed" | "skipped_safety" | "skipped_early_stop" | "error"
    reason: str = ""
    identity: str = ""
    result: Any = None
    critical: bool = False


# Type aliases for the injected callables.
Runner = Callable[[RunContext], Awaitable[Any]]
IsCritical = Callable[[Any], bool]
ResetHook = Callable[[str, RunContext], Awaitable[None]]


def _default_is_critical(result: Any) -> bool:
    return bool(getattr(result, "critical", False))


def _reuses_state(obj: ScenarioObjective) -> bool:
    return obj.state_impact in _STATE_REUSING_STATES or obj.family in _STATE_REUSING_FAMILIES


def _reuse_key(obj: ScenarioObjective) -> str:
    """Stable identity-group key for state-reusing objectives."""
    for lock in sorted(obj.resource_locks):
        if lock.startswith(("identity:", "memory:")):
            return lock
    return f"{obj.family}:{','.join(sorted(obj.surface_node_ids))}"


class PhasedScheduler:
    """Drives objective execution in safe, attacker-like phases."""

    def __init__(
        self,
        *,
        concurrency: int = 5,
        safety: SafetyPolicy | None = None,
        stop_on_critical: bool = True,
        identity_factory: Callable[[], str] | None = None,
        objective_timeout: float = 120.0,
    ) -> None:
        self._concurrency = max(1, concurrency)
        self._safety = safety or SafetyPolicy()
        self._stop_on_critical = stop_on_critical
        self._identity_factory = identity_factory or (lambda: f"rt2-{uuid.uuid4().hex[:12]}")
        # 0 means no timeout
        self._objective_timeout: float | None = objective_timeout if objective_timeout > 0 else None

    async def run(
        self,
        objectives: Iterable[ScenarioObjective],
        runner: Runner,
        *,
        is_critical: IsCritical | None = None,
        on_reset: ResetHook | None = None,
    ) -> list[ScheduledResult]:
        """Execute *objectives* phase-by-phase and return per-objective results."""
        is_critical = is_critical or _default_is_critical
        by_phase: dict[Phase, list[ScenarioObjective]] = defaultdict(list)
        for obj in objectives:
            by_phase[phase_from_int(obj.execution_phase)].append(obj)

        results: list[ScheduledResult] = []
        locks: dict[str, asyncio.Lock] = {}
        reuse_identities: dict[str, str] = {}
        critical_seen = False

        for phase in sorted(by_phase):
            phase_objs = by_phase[phase]
            # Early-stop: skip high-impact phases once a critical signal is seen.
            if critical_seen and self._stop_on_critical and phase in HIGH_IMPACT_PHASES:
                for obj in phase_objs:
                    results.append(
                        ScheduledResult(
                            objective=obj,
                            phase=phase,
                            status="skipped_early_stop",
                            reason="critical side effect observed in an earlier phase",
                        )
                    )
                continue

            sem = asyncio.Semaphore(self._concurrency)
            phase_results = await asyncio.gather(
                *(
                    self._run_one(obj, phase, runner, is_critical, on_reset, sem, locks, reuse_identities)
                    for obj in phase_objs
                )
            )
            results.extend(phase_results)
            if any(r.critical for r in phase_results):
                critical_seen = True

            # Abort remaining phases when every completed objective in this
            # phase failed due to HTTP 4xx transport errors (e.g. 405 Method
            # Not Allowed), which indicates the target endpoint is
            # misconfigured — not that the target defended against the attack.
            completed_phase = [r for r in phase_results if r.status == "completed"]
            transport_errors = [
                r for r in completed_phase
                if getattr(r.result, "target_transport_error", False)
            ]
            if completed_phase and len(transport_errors) == len(completed_phase):
                _log.error(
                    "All %d objectives in phase %s returned HTTP 4xx/5xx (transport error) — "
                    "target endpoint appears misconfigured or unavailable. Aborting remaining phases.",
                    len(transport_errors),
                    phase,
                )
                remaining_phases = sorted(k for k in by_phase if k > phase)
                for remaining_phase in remaining_phases:
                    for obj in by_phase[remaining_phase]:
                        results.append(
                            ScheduledResult(
                                objective=obj,
                                phase=remaining_phase,
                                status="skipped_transport_error",
                                reason=(
                                    f"target returned HTTP 4xx for all objectives "
                                    f"in phase {phase} — endpoint misconfigured"
                                ),
                            )
                        )
                break

        _log.info(
            "scheduler complete: %d objectives across %d phases", len(results), len(by_phase)
        )
        return results

    async def _run_one(
        self,
        obj: ScenarioObjective,
        phase: Phase,
        runner: Runner,
        is_critical: IsCritical,
        on_reset: ResetHook | None,
        sem: asyncio.Semaphore,
        locks: dict[str, asyncio.Lock],
        reuse_identities: dict[str, str],
    ) -> ScheduledResult:
        decision = assess(obj, self._safety)
        if not decision.allowed:
            _log.debug(
                "safety: skipping objective %s (%s) — %s",
                obj.objective_id, obj.family, decision.reason,
            )
            return ScheduledResult(
                objective=obj, phase=phase, status="skipped_safety", reason=decision.reason
            )

        identity, fresh = self._assign_identity(obj, reuse_identities)
        ctx = RunContext(objective=obj, phase=phase, identity=identity, fresh_identity=fresh)

        async with sem:
            acquired = await self._acquire_locks(obj, locks)
            try:
                if self._objective_timeout is not None:
                    result = await asyncio.wait_for(
                        runner(ctx), timeout=self._objective_timeout
                    )
                else:
                    result = await runner(ctx)
                crit = is_critical(result)
                status = "completed"
                reason = ""
            except asyncio.TimeoutError:
                _log.warning(
                    "objective %s timed out after %.0fs",
                    obj.objective_id,
                    self._objective_timeout,
                )
                result, crit, status, reason = (
                    None, False, "timeout",
                    f"timed out after {self._objective_timeout:.0f}s",
                )
            except Exception as exc:  # a single objective failure never aborts the run
                _log.warning("objective %s failed: %s", obj.objective_id, exc)
                result, crit, status, reason = None, False, "error", str(exc)
            finally:
                for lock in reversed(acquired):
                    lock.release()

        # Surface reset hooks after any state-mutating objective.
        if on_reset and obj.state_impact != StateImpact.NONE.value:
            for hook in obj.reset_hooks:
                try:
                    await on_reset(hook, ctx)
                except Exception as exc:  # reset failures are logged, not fatal
                    _log.warning("reset hook %r failed for %s: %s", hook, obj.objective_id, exc)

        return ScheduledResult(
            objective=obj,
            phase=phase,
            status=status,
            reason=reason,
            identity=identity,
            result=result,
            critical=crit,
        )

    def _assign_identity(
        self, obj: ScenarioObjective, reuse_identities: dict[str, str]
    ) -> tuple[str, bool]:
        if _reuses_state(obj):
            key = _reuse_key(obj)
            if key not in reuse_identities:
                reuse_identities[key] = self._identity_factory()
                return reuse_identities[key], True
            return reuse_identities[key], False
        return self._identity_factory(), True

    @staticmethod
    async def _acquire_locks(
        obj: ScenarioObjective, locks: dict[str, asyncio.Lock]
    ) -> list[asyncio.Lock]:
        acquired: list[asyncio.Lock] = []
        for key in sorted(obj.resource_locks):
            lock = locks.setdefault(key, asyncio.Lock())
            await lock.acquire()
            acquired.append(lock)
        return acquired
