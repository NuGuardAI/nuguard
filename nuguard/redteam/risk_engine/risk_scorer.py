"""Aggregates finding severities into a composite risk score."""
from __future__ import annotations

from nuguard.models.finding import Finding, Severity

_WEIGHTS = {
    Severity.CRITICAL: 9.0,
    Severity.HIGH: 7.0,
    Severity.MEDIUM: 4.0,
    Severity.LOW: 1.0,
    Severity.INFO: 0.0,
}


def aggregate_score(findings: list[Finding]) -> float:
    """Return a [0, 10] risk score from a list of findings."""
    if not findings:
        return 0.0
    total = sum(_WEIGHTS.get(f.severity, 0.0) for f in findings)
    max_possible = len(findings) * 10.0
    return round((total / max_possible) * 10.0, 2) if max_possible > 0 else 0.0


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
