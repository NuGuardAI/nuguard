"""Non-negotiable safety enforcement for the phased scheduler.

Encodes the design's safety rules at the objective level:

* destructive / external-write objectives may only run behind a non-live
  ``safe_execution`` mode (dry-run, emulated, trap endpoint, sandbox, synthetic
  tenant);
* when ``dry_run_only`` is set, destructive state impact is allowed only through
  dry-run/emulated/sandbox modes;
* external egress is permitted only against NuGuard-controlled trap endpoints.

The check is pure (no I/O).  Reset-hook *invocation* is delegated to a caller-
supplied callback so the scheduler stays decoupled from concrete fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.redteam.v2.knowledge.schema import StateImpact

# Safe-execution modes that do not touch live production surfaces.
_NON_LIVE_MODES: frozenset[str] = frozenset(
    {"dry_run_tool", "emulated_tool", "trap_endpoint", "sandbox", "synthetic_tenant", "trace_only", "canary_only"}
)
# Modes acceptable for genuinely destructive actions (no real side effects).
_DESTRUCTIVE_SAFE_MODES: frozenset[str] = frozenset(
    {"dry_run_tool", "emulated_tool", "sandbox"}
)
# Modes acceptable for external egress (must be a controlled sink).
_EGRESS_SAFE_MODES: frozenset[str] = frozenset({"trap_endpoint", "dry_run_tool", "emulated_tool", "sandbox"})

_HIGH_IMPACT_STATES: frozenset[str] = frozenset(
    {StateImpact.EXTERNAL_WRITE.value, StateImpact.DESTRUCTIVE.value}
)


@dataclass(frozen=True)
class SafetyPolicy:
    """Run-level safety configuration."""

    dry_run_only: bool = True
    allow_external_egress: bool = True  # to trap endpoints only


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str = ""


def assess(objective: object, policy: SafetyPolicy) -> SafetyDecision:
    """Return whether *objective* is safe to execute under *policy*.

    Accepts any object exposing ``state_impact`` and ``safe_execution`` strings
    (e.g. a :class:`ScenarioObjective`).
    """
    state = str(getattr(objective, "state_impact", "none"))
    mode = str(getattr(objective, "safe_execution", "trace_only"))

    if state == StateImpact.DESTRUCTIVE.value:
        if mode not in _DESTRUCTIVE_SAFE_MODES:
            return SafetyDecision(
                False,
                f"destructive action requires a dry-run/emulated/sandbox mode, got {mode!r}",
            )
    elif state == StateImpact.EXTERNAL_WRITE.value:
        if not policy.allow_external_egress:
            return SafetyDecision(False, "external egress disabled by policy")
        if mode not in _EGRESS_SAFE_MODES:
            return SafetyDecision(
                False,
                f"external egress requires a trap/dry-run/emulated/sandbox mode, got {mode!r}",
            )

    if state in _HIGH_IMPACT_STATES and mode not in _NON_LIVE_MODES:
        return SafetyDecision(False, f"high-impact state {state!r} requires a non-live mode")

    if policy.dry_run_only and state == StateImpact.DESTRUCTIVE.value and mode not in _DESTRUCTIVE_SAFE_MODES:
        return SafetyDecision(False, "dry_run_only: destructive action not in dry-run mode")

    return SafetyDecision(True)


@dataclass
class ResetLedger:
    """Tracks reset hooks that should run after state-mutating objectives."""

    pending: list[str] = field(default_factory=list)

    def note(self, hooks: tuple[str, ...]) -> None:
        self.pending.extend(hooks)

    def clear(self) -> list[str]:
        done = list(self.pending)
        self.pending.clear()
        return done
