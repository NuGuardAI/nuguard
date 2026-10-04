"""Breadth / depth scheduling, budgets and redundancy suppression.

Three passes (v5 spec §7): **breadth** (one representative meaningful attempt per
applicable control x channel x identity boundary, in level order, with a cap on
variants), **depth** (expand techniques/targets, prioritised by uncovered
controls, new evidence, risk and expected information per request — never halted
by a finding), and **confirmation** (reserved capacity, handled elsewhere).
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from nuguard.redteam.catalog.scheduling import SchedulingMeta

from .branches import ObjectiveRequirements
from .ledger import CoverageLedger, LedgerEntry, Status


@dataclass
class Objective:
    """A schedulable catalog objective."""

    objective_id: str
    catalog_id: str
    scenario: Any                      # AttackScenario
    meta: SchedulingMeta
    req: ObjectiveRequirements
    identity_boundary: str = "own_account"
    target: str = ""
    est_requests: int = 3
    impact: float = 5.0
    payload_fingerprint: str = ""      # normalized first payload (for redundancy)
    precondition: str = ""             # branch precondition (e.g. "warm", "tainted:instruction_override")

    @property
    def group_key(self) -> tuple[str, str, str]:
        return (self.meta.control_id, self.meta.resource_scope, self.identity_boundary)


# -- budgets -----------------------------------------------------------------

@dataclass
class BudgetTracker:
    """Run-level budgets; ``None`` means unlimited (no hidden cap)."""

    max_requests: int | None = None
    max_seconds: float | None = None
    max_llm_cost_usd: float | None = None
    reserve_fraction: float = 0.2
    clock: Callable[[], float] = time.monotonic
    requests_used: int = 0
    llm_cost_used: float = 0.0
    _t0: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._t0 = self.clock()

    @property
    def finite(self) -> bool:
        return any(v is not None for v in (self.max_requests, self.max_seconds, self.max_llm_cost_usd))

    def spend(self, requests: int = 0, llm_cost: float = 0.0) -> None:
        self.requests_used += requests
        self.llm_cost_used += llm_cost

    def exhausted(self, *, reserved: bool = False) -> bool:
        """True when no budget remains. ``reserved`` work may dip into the reserve."""
        frac = 1.0 if reserved else 1.0 - self.reserve_fraction
        if self.max_requests is not None and self.requests_used >= self.max_requests * frac:
            return True
        if self.max_seconds is not None and (self.clock() - self._t0) >= self.max_seconds * frac:
            return True
        return bool(
            self.max_llm_cost_usd is not None and self.llm_cost_used >= self.max_llm_cost_usd * frac
        )

    def request_headroom(self) -> int | None:
        """Requests available to breadth/depth (reserve excluded); ``None`` = unlimited."""
        if self.max_requests is None:
            return None
        return max(0, int(self.max_requests * (1.0 - self.reserve_fraction)) - self.requests_used)


# -- redundancy ----------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize_payload(text: str) -> str:
    return _WS.sub(" ", text.strip().lower())


def payload_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_payload(text).encode()).hexdigest()[:16]


class DedupIndex:
    """Suppress equivalent setup/payloads; suppressed objectives are ``redundant``, not run.

    Key: control, channel, principal scope, normalized payload, target, branch
    precondition. Similar refusal text across *different* controls/channels never
    collapses them — those differ in the key.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, ...], str] = {}

    @staticmethod
    def key(o: Objective) -> tuple[str, ...]:
        return (
            o.meta.control_id, o.meta.resource_scope, o.req.principal_ref,
            o.payload_fingerprint, o.target, o.precondition,
        )

    def check(self, o: Objective) -> str | None:
        """Return the id of the equivalent objective already scheduled, else register *o*."""
        if not o.payload_fingerprint:
            return None
        k = self.key(o)
        prior = self._seen.get(k)
        if prior is not None and prior != o.objective_id:
            return prior
        self._seen[k] = o.objective_id
        return None


# -- scheduler ------------------------------------------------------------------

@dataclass
class BreadthPlan:
    selected: list[Objective]
    deferred: list[Objective]
    fits: bool                          # False -> publish unresolved coverage before starting


class CampaignScheduler:
    """Breadth first, then depth; failures on one technique never block another."""

    UNINFORMATIVE_LIMIT = 2             # consecutive uninformative attempts end a technique branch

    def __init__(
        self,
        ledger: CoverageLedger,
        budget: BudgetTracker | None = None,
        dedup: DedupIndex | None = None,
    ) -> None:
        self.ledger = ledger
        self.budget = budget or BudgetTracker()
        self.dedup = dedup or DedupIndex()
        self._uninformative: dict[tuple[str, str], int] = {}
        self._retired_techniques: set[tuple[str, str]] = set()

    # -- registration ------------------------------------------------------
    def register(self, objectives: list[Objective]) -> list[Objective]:
        """Add objectives to the ledger; equivalent ones become ``redundant`` (with a ref)."""
        live: list[Objective] = []
        for o in objectives:
            self.ledger.register(LedgerEntry(
                o.objective_id, o.catalog_id, o.meta.control_id, o.meta.technique_class,
                o.meta.resource_scope, o.identity_boundary, o.meta.primary_level,
                status=Status.APPLICABLE,
            ))
            prior = self.dedup.check(o)
            if prior is not None:
                self.ledger.set_status(o.objective_id, Status.REDUNDANT, ref=prior)
                continue
            live.append(o)
        return live

    # -- breadth ---------------------------------------------------------------
    def plan_breadth(self, objectives: list[Objective], max_variants: int = 1) -> BreadthPlan:
        """One representative per control x channel x identity boundary, in level order."""
        groups: dict[tuple[str, str, str], list[Objective]] = {}
        for o in objectives:
            groups.setdefault(o.group_key, []).append(o)
        reps: list[Objective] = []
        for members in groups.values():
            members.sort(key=lambda o: (-o.impact, o.catalog_id, o.objective_id))
            reps.extend(members[:max_variants])
        reps.sort(key=lambda o: (o.meta.primary_level, -o.impact, o.catalog_id))

        headroom = self.budget.request_headroom()
        if headroom is None:
            return BreadthPlan(reps, [], True)
        selected: list[Objective] = []
        deferred: list[Objective] = []
        used = 0
        for o in reps:
            if used + o.est_requests <= headroom:
                selected.append(o)
                used += o.est_requests
            else:
                deferred.append(o)
                self.ledger.set_status(
                    o.objective_id, Status.BUDGET_DEFERRED, reason="breadth_does_not_fit_budget"
                )
        return BreadthPlan(selected, deferred, fits=not deferred)

    # -- depth -----------------------------------------------------------------
    def eligible(self, o: Objective) -> bool:
        return (o.meta.control_id, o.meta.technique_class) not in self._retired_techniques

    def next_depth(self, remaining: list[Objective], new_evidence: set[str] | None = None) -> list[Objective]:
        """Order remaining objectives by information gain per request.

        Priority favours uncovered controls, objectives that consume fresh
        evidence (e.g. a newly discovered tool schema), higher risk, and cheaper
        requests. There is no halt on severity: distinct objectives continue
        even after a critical finding.
        """
        covered = self.ledger.covered_controls()
        evidence = new_evidence or set()

        def score(o: Objective) -> float:
            gain = 3.0 if o.meta.control_id not in covered else 0.0
            gain += 2.0 if o.catalog_id in evidence else 0.0
            gain += o.impact / 5.0
            return gain / max(1, o.est_requests)

        return sorted((o for o in remaining if self.eligible(o)), key=lambda o: -score(o))

    # -- outcomes ----------------------------------------------------------------
    def record_outcome(self, o: Objective, *, informative: bool, meaningful: bool = True) -> None:
        """Record an attempt; two consecutive uninformative attempts end only that technique branch."""
        self.ledger.record_attempt(o.objective_id, meaningful=meaningful)
        key = (o.meta.control_id, o.meta.technique_class)
        if informative:
            self._uninformative[key] = 0
            return
        n = self._uninformative.get(key, 0) + 1
        self._uninformative[key] = n
        if n >= self.UNINFORMATIVE_LIMIT:
            self._retired_techniques.add(key)
