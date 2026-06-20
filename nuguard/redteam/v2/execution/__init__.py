"""Phase 5 — adaptive execution (thin layer over v1 executors).

:class:`ObjectiveRunner` synthesises a concrete ``AttackScenario`` from a
:class:`ScenarioObjective` (via the linked catalog builder), composes kill chains
from prior-phase successes, runs it through the v1 ``AttackExecutor`` /
``GuidedAttackExecutor``, and returns a deterministic :class:`ObjectiveOutcome`.
It is the scheduler's ``runner`` callable.
"""
from __future__ import annotations

from nuguard.redteam.v2.execution.runner import (
    KillChainState,
    ObjectiveOutcome,
    ObjectiveRunner,
    SupportsGuidedRun,
    SupportsStaticRun,
)

__all__ = [
    "KillChainState",
    "ObjectiveOutcome",
    "ObjectiveRunner",
    "SupportsGuidedRun",
    "SupportsStaticRun",
]
