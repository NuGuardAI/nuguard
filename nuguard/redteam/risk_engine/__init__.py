"""Risk engine: severity scoring and compliance mapping.

Severity scoring lives in :mod:`nuguard.redteam.risk_engine.ngrs` (the NGRS
Impact x Likelihood model — see that module's docstring for why it replaced
the old flat goal-type lookup table). Remediation generation lives in
:mod:`nuguard.remediation` (shared by behavior, redteam, and analysis).
"""
from .compliance_mapper import owasp_asi_ref, owasp_llm_ref
from .ngrs import (
    ImpactFactors,
    LikelihoodFactors,
    NGRSResult,
    rescore_with_probe,
    score_finding,
    score_policy_violation,
)
from .ngrs import score as score_ngrs
from .risk_scorer import aggregate_score, highest_severity

__all__ = [
    "score_finding",
    "score_policy_violation",
    "score_ngrs",
    "rescore_with_probe",
    "ImpactFactors",
    "LikelihoodFactors",
    "NGRSResult",
    "owasp_llm_ref",
    "owasp_asi_ref",
    "aggregate_score",
    "highest_severity",
]
