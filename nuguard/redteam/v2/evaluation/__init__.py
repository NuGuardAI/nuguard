"""Phase 6 — layered evaluation pipeline (false-positive reduction core).

:class:`EvaluationPipeline` runs deterministic detectors → semantic multi-judge →
side-effect verifier → robustness guard, then a cross-objective transferability
pass, escalating to the LLM only when cheaper layers are inconclusive.  It emits
a :class:`Verdict` per objective.
"""
from __future__ import annotations

from nuguard.redteam.v2.evaluation.deterministic import DeterministicEvaluator
from nuguard.redteam.v2.evaluation.pipeline import EvaluationPipeline
from nuguard.redteam.v2.evaluation.robustness import RobustnessGuard
from nuguard.redteam.v2.evaluation.semantic_judge import JudgeVote, SemanticJudge
from nuguard.redteam.v2.evaluation.side_effect import SideEffectVerifier
from nuguard.redteam.v2.evaluation.transferability import TransferabilityScorer
from nuguard.redteam.v2.evaluation.verdict import (
    Confidence,
    EvaluationInput,
    LayerResult,
    Verdict,
)

__all__ = [
    "Confidence",
    "DeterministicEvaluator",
    "EvaluationInput",
    "EvaluationPipeline",
    "JudgeVote",
    "LayerResult",
    "RobustnessGuard",
    "SemanticJudge",
    "SideEffectVerifier",
    "TransferabilityScorer",
    "Verdict",
]
