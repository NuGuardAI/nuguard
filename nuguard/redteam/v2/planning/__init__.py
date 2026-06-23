"""Phase 3 — coverage matrix and scenario objective generation.

* :func:`generate_objectives` turns an :class:`AttackSurface` + KB + Cognitive
  Policy into a list of :class:`ScenarioObjective` (intent + judging metadata,
  not prompt strings) plus a :class:`CoverageMatrix`.
* ART behaviour categories (:class:`BehaviorCategory`) are explicit dimensions.
"""
from __future__ import annotations

from nuguard.redteam.v2.planning.coverage_matrix import (
    CoverageEntry,
    CoverageMatrix,
    CoverageStatus,
)
from nuguard.redteam.v2.planning.objective_generator import (
    BehaviorCategory,
    ObjectiveIntent,
    ScenarioObjective,
    generate_objectives,
)

__all__ = [
    "BehaviorCategory",
    "CoverageEntry",
    "CoverageMatrix",
    "CoverageStatus",
    "ObjectiveIntent",
    "ScenarioObjective",
    "generate_objectives",
]
