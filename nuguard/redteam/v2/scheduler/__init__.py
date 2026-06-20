"""Phase 4 — phased scheduler (phases 0-10) and safety guards.

* :class:`Phase` / :data:`PHASE_INFO` encode the design's attacker-like stages.
* :class:`PhasedScheduler` orders objectives by phase, serializes by resource
  lock, runs independent objectives concurrently, mints fresh identities, applies
  safety screening, and early-stops high-impact phases on a critical signal.
* :func:`assess` / :class:`SafetyPolicy` enforce dry-run-only and non-live
  execution for destructive/external-write objectives.
"""
from __future__ import annotations

from nuguard.redteam.v2.scheduler.phases import (
    HIGH_IMPACT_PHASES,
    PHASE_INFO,
    PREREQUISITE_PHASES,
    Phase,
    PhaseInfo,
    phase_from_int,
)
from nuguard.redteam.v2.scheduler.safety import (
    ResetLedger,
    SafetyDecision,
    SafetyPolicy,
    assess,
)
from nuguard.redteam.v2.scheduler.scheduler import (
    PhasedScheduler,
    RunContext,
    ScheduledResult,
)

__all__ = [
    "HIGH_IMPACT_PHASES",
    "PHASE_INFO",
    "PREREQUISITE_PHASES",
    "Phase",
    "PhaseInfo",
    "PhasedScheduler",
    "ResetLedger",
    "RunContext",
    "SafetyDecision",
    "SafetyPolicy",
    "ScheduledResult",
    "assess",
    "phase_from_int",
]
