"""Risk engine: severity scoring and compliance mapping.

Remediation generation lives in :mod:`nuguard.remediation` (shared by
behavior, redteam, and analysis).
"""
from .compliance_mapper import owasp_asi_ref, owasp_llm_ref
from .risk_scorer import aggregate_score, highest_severity
from .severity_scorer import score_finding

__all__ = [
    "score_finding",
    "owasp_llm_ref",
    "owasp_asi_ref",
    "aggregate_score",
    "highest_severity",
]
