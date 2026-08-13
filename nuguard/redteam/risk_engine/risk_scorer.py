"""Aggregates finding severities into a composite risk score."""
from __future__ import annotations

from nuguard.models.finding import Finding, Severity

# Fallback weights (on the same 0-10 scale as an NGRS score / 10) for
# findings that predate NGRS scoring or otherwise lack `ngrs_score` (e.g.
# findings constructed directly by tests). Once every Finding carries an
# `ngrs_score`, this table only matters as a safety net.
_SEVERITY_FALLBACK_WEIGHTS = {
    Severity.CRITICAL: 9.0,
    Severity.HIGH: 7.0,
    Severity.MEDIUM: 4.0,
    Severity.LOW: 1.0,
    Severity.INFO: 0.0,
}


def aggregate_score(findings: list[Finding]) -> float:
    """Return a [0, 10] composite risk score, averaging each finding's NGRS score.

    Uses ``finding.ngrs_score`` (0-100, see
    :mod:`nuguard.redteam.risk_engine.ngrs`) when present, scaled to the
    [0, 10] range this function has always returned; falls back to a flat
    severity-weight table for findings with no NGRS score. This is the
    single source of truth for the redteam report's overall risk score —
    previously ``nuguard.redteam.report.to_markdown`` computed its own,
    differently-weighted mean independently of this function.
    """
    if not findings:
        return 0.0
    total = sum(
        (f.ngrs_score / 10.0) if f.ngrs_score is not None else _SEVERITY_FALLBACK_WEIGHTS.get(f.severity, 0.0)
        for f in findings
    )
    return round(total / len(findings), 2)


def highest_severity(findings: list[Finding]) -> Severity | None:
    """Return the highest severity among all findings, or None if empty."""
    if not findings:
        return None
    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    sevs = {f.severity for f in findings}
    for s in order:
        if s in sevs:
            return s
    return None
