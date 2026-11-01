"""Conversation branches and their lifecycle.

A *branch* is one target conversation: a local :class:`AttackSession`, the
:class:`BranchTransport` holding the server-issued ids/cookies/headers, the
:class:`Principal` it is pinned to, and a contamination state. States::

    new -> ready -> probing -> tainted -> retired
                 \\-> recovering / expired (from any active state)

Reuse is deliberate (v5 spec §6): a branch serves the next objective only when
identity, history cleanliness and contamination are compatible. A clean branch
is *not* a cheap fork — a new branch means a new target conversation plus the
minimum benign setup replayed.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from nuguard.redteam.catalog.scheduling import SessionPolicy
from nuguard.redteam.target.session import AttackSession

from .config import CampaignConfig
from .transport import BranchTransport, Principal


class BranchState(str, Enum):
    NEW = "new"
    READY = "ready"            # baseline done, history clean enough to reuse
    PROBING = "probing"        # in use by attack objectives, still compatible
    TAINTED = "tainted"        # an attack was accepted; only measuring objectives may continue
    RECOVERING = "recovering"
    EXPIRED = "expired"
    RETIRED = "retired"


class RotationReason(str, Enum):
    IDENTITY_CHANGE = "identity_change"
    INCOMPATIBLE_TAINT = "incompatible_taint"
    PERSISTENT_WRITE = "persistent_write"
    TURN_LIMIT = "turn_limit"
    TOKEN_LIMIT = "token_limit"
    UNKNOWN_STATE = "unknown_state_after_transport_error"
    CAMPAIGN_COMPLETE = "campaign_complete"
    ISOLATED_OBJECTIVE = "isolated_objective"


_ACTIVE = (BranchState.NEW, BranchState.READY, BranchState.PROBING, BranchState.TAINTED)


@dataclass(frozen=True)
class ObjectiveRequirements:
    """What an objective needs from a branch."""

    principal_ref: str
    session_policy: SessionPolicy = SessionPolicy.REUSE
    #: Tags this objective may taint the branch with when its attack is accepted.
    taints: tuple[str, ...] = ()
    #: True when the objective explicitly measures the compromised context
    #: (e.g. "does the override persist?"): it may continue on a tainted branch.
    measures_taint: tuple[str, ...] = ()
    persistent_write: bool = False


@dataclass
class Branch:
    """One conversation branch (see module docstring)."""

    branch_id: str
    principal: Principal
    transport: BranchTransport
    session: AttackSession
    state: BranchState = BranchState.NEW
    parent_id: str | None = None
    generation: int = 0
    contamination: set[str] = field(default_factory=set)
    active_objective: str | None = None
    baseline_done: bool = False
    attack_turns: int = 0          # turns used by attack objectives (baseline excluded)
    objective_history: list[str] = field(default_factory=list)
    retire_reason: RotationReason | None = None

    @property
    def clean(self) -> bool:
        return not self.contamination and self.attack_turns == 0


class BranchManager:
    """Creates, reuses, rotates and retires branches under a :class:`CampaignConfig`."""

    def __init__(
        self,
        config: CampaignConfig,
        session_factory: Callable[[str], AttackSession],
        *,
        id_prefix: str = "b",
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._seq = itertools.count(1)
        self._prefix = id_prefix
        self.branches: dict[str, Branch] = {}

    # -- creation -----------------------------------------------------------
    def new_branch(self, principal: Principal, parent: Branch | None = None) -> Branch:
        bid = f"{self._prefix}{next(self._seq)}"
        transport = BranchTransport(bid)
        transport.pin_principal(principal)
        branch = Branch(
            branch_id=bid,
            principal=principal,
            transport=transport,
            session=self._session_factory(bid),
            parent_id=parent.branch_id if parent else None,
            generation=(parent.generation + 1) if parent else 0,
        )
        self.branches[bid] = branch
        return branch

    # -- compatibility ------------------------------------------------------
    def rotation_reason(
        self, branch: Branch, req: ObjectiveRequirements | None = None, next_tokens: int = 0
    ) -> RotationReason | None:
        """Why *branch* must not serve *req* (or continue at all), else ``None``."""
        if branch.state not in _ACTIVE:
            return RotationReason.CAMPAIGN_COMPLETE
        if (
            branch.transport.turns >= self._config.max_turns_per_branch
        ):
            return RotationReason.TURN_LIMIT
        if branch.transport.estimated_tokens + next_tokens >= self._config.max_branch_tokens:
            return RotationReason.TOKEN_LIMIT
        if req is None:
            return None
        if req.principal_ref != branch.principal.ref:
            return RotationReason.IDENTITY_CHANGE
        if req.session_policy == SessionPolicy.ISOLATED:
            return RotationReason.ISOLATED_OBJECTIVE
        if branch.contamination and not set(branch.contamination) <= set(req.measures_taint):
            return RotationReason.INCOMPATIBLE_TAINT
        if req.session_policy == SessionPolicy.FRESH and not branch.clean:
            return RotationReason.INCOMPATIBLE_TAINT
        return None

    def acquire(
        self, principal: Principal, req: ObjectiveRequirements, next_tokens: int = 0
    ) -> Branch:
        """Return a compatible live branch, or a new one (never mixing principals)."""
        for branch in self.branches.values():
            if self.rotation_reason(branch, req, next_tokens) is None:
                branch.active_objective = "pending"
                return branch
        branch = self.new_branch(principal)
        branch.active_objective = "pending"
        return branch

    # -- lifecycle ----------------------------------------------------------
    def mark_baseline_done(self, branch: Branch) -> None:
        branch.baseline_done = True
        if branch.state == BranchState.NEW:
            branch.state = BranchState.READY

    def needs_baseline(self, branch: Branch) -> bool:
        """One baseline per compatible branch — never repeated on reuse."""
        return not branch.baseline_done

    def begin(self, branch: Branch, objective_ref: str) -> None:
        branch.active_objective = objective_ref
        if branch.state in (BranchState.NEW, BranchState.READY):
            branch.state = BranchState.PROBING

    def release(
        self,
        branch: Branch,
        objective_ref: str,
        req: ObjectiveRequirements,
        *,
        attack_turns: int,
        attack_accepted: bool = False,
        unknown_state: bool = False,
    ) -> RotationReason | None:
        """Record an objective's outcome; retire the branch when rotation is required."""
        branch.active_objective = None
        branch.attack_turns += attack_turns
        branch.objective_history.append(objective_ref)
        if attack_accepted and req.taints:
            branch.contamination.update(req.taints)
            branch.state = BranchState.TAINTED
        reason: RotationReason | None = None
        if unknown_state:
            reason = RotationReason.UNKNOWN_STATE
        elif req.persistent_write:
            reason = RotationReason.PERSISTENT_WRITE
        elif req.session_policy == SessionPolicy.ISOLATED:
            reason = RotationReason.ISOLATED_OBJECTIVE
        else:
            reason = self.rotation_reason(branch)
        if reason is not None:
            self.retire(branch, reason)
        return reason

    def retire(self, branch: Branch, reason: RotationReason) -> None:
        branch.state = BranchState.RETIRED
        branch.retire_reason = reason

    def complete_campaign(self) -> None:
        for b in self.branches.values():
            if b.state != BranchState.RETIRED:
                self.retire(b, RotationReason.CAMPAIGN_COMPLETE)

    def live(self) -> list[Branch]:
        return [b for b in self.branches.values() if b.state in _ACTIVE]
