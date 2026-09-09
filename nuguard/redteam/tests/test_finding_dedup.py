"""Tests for evidence-similarity finding dedup.

``_dedup_findings`` (exact-key: finding_id/goal_type/affected_component) only
collapses findings whose *title* slugs to the same finding_id. Differently
titled scenarios that hit the same endpoint with near-identical evidence
(e.g. "JWT Tampering", "Authentication Bypass", "Auth Scope Bypass" all
observing the same unauthenticated GET /api/agents response) survive that
pass as separate findings, inflating severity/count signal for one real
issue. ``_dedup_findings_by_evidence_similarity`` is a second pass that
collapses those on evidence-text similarity, scoped to same
(affected_component, goal_type) so distinct attack classes are never merged.
"""
from __future__ import annotations

from nuguard.models.finding import Finding, Severity
from nuguard.redteam.executor.orchestrator import _dedup_findings_by_evidence_similarity


def _finding(
    title: str,
    *,
    severity: Severity = Severity.HIGH,
    affected_component: str | None = "GET /api/agents",
    goal_type: str = "AUTH_BYPASS",
    evidence: str = "",
) -> Finding:
    return Finding(
        finding_id=title.lower().replace(" ", "-"),
        title=title,
        severity=severity,
        description="",
        affected_component=affected_component,
        goal_type=goal_type,
        evidence=evidence,
    )


_SHARED_EVIDENCE = (
    "Unauthenticated GET /api/agents returned HTTP 200 with the full internal "
    "agent topology including tool names, MCP service endpoints, and system "
    "prompt excerpts, with no auth header or cookie sent."
)


def test_dedup_collapses_same_component_similar_evidence_different_titles() -> None:
    findings = [
        _finding("JWT Tampering", severity=Severity.HIGH, evidence=_SHARED_EVIDENCE),
        _finding("Authentication Bypass", severity=Severity.HIGH, evidence=_SHARED_EVIDENCE),
        _finding("Auth Scope Bypass", severity=Severity.CRITICAL, evidence=_SHARED_EVIDENCE),
    ]

    result = _dedup_findings_by_evidence_similarity(findings)

    assert len(result) == 1
    survivor = result[0]
    # Highest severity among the cluster wins.
    assert survivor.severity == Severity.CRITICAL
    assert "collapsed 2 near-duplicate" in survivor.description
    assert "JWT Tampering" in survivor.description
    assert "Authentication Bypass" in survivor.description


def test_dedup_does_not_collapse_different_goal_types() -> None:
    findings = [
        _finding("JWT Tampering", goal_type="AUTH_BYPASS", evidence=_SHARED_EVIDENCE),
        _finding("Object Read", goal_type="IDOR", evidence=_SHARED_EVIDENCE),
    ]

    result = _dedup_findings_by_evidence_similarity(findings)

    assert len(result) == 2


def test_dedup_does_not_collapse_dissimilar_evidence_same_component() -> None:
    findings = [
        _finding(
            "Missing Auth Header",
            evidence="Response returned 200 with no Authorization header required at all.",
        ),
        _finding(
            "Mass Assignment Field Injection",
            evidence=(
                "Injected an unexpected 'is_admin' field into the request body and "
                "the target silently accepted and persisted it, escalating the "
                "account's role without any validation."
            ),
        ),
    ]

    result = _dedup_findings_by_evidence_similarity(findings)

    assert len(result) == 2


def test_dedup_handles_empty_evidence_safely() -> None:
    findings = [
        _finding("Finding A", evidence=""),
        _finding("Finding B", evidence=""),
    ]

    result = _dedup_findings_by_evidence_similarity(findings)

    # Empty evidence never clusters — no crash, no over-merge.
    assert len(result) == 2


def test_dedup_never_merges_across_empty_affected_component() -> None:
    findings = [
        _finding("Finding A", affected_component=None, evidence=_SHARED_EVIDENCE),
        _finding("Finding B", affected_component=None, evidence=_SHARED_EVIDENCE),
    ]

    result = _dedup_findings_by_evidence_similarity(findings)

    assert len(result) == 2
