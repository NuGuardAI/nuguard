from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from vulnerableapp_benchmark import build_findings  # noqa: E402


def _finding(**overrides: object) -> dict:
    base = {
        "canonical_path": "/ErrorBasedSQLInjectionVulnerability/LEVEL_1",
        "correlation_status": "mutation_reconciled",
        "cwe_ids": ["CWE-89"],
        "vulnerability_types": ["ERROR_BASED_SQL_INJECTION"],
        "matched_at": "https://example.com/VulnerableApp/ErrorBasedSQLInjectionVulnerability;/LEVEL_1",
        "metadata": {},
    }
    base.update(overrides)
    return base


def test_canonical_path_strips_configured_context_exactly_once() -> None:
    findings, summary = build_findings(
        {"findings": [_finding()]}, base_path="/VulnerableApp"
    )
    assert findings == [{"url": "/ErrorBasedSQLInjectionVulnerability/LEVEL_1", "type": "ERROR_BASED_SQL_INJECTION", "cwe": "CWE-89"}]
    assert summary.findings_exported == 1


def test_query_string_does_not_affect_exported_url() -> None:
    findings, _ = build_findings(
        {"findings": [_finding(canonical_path="/ErrorBasedSQLInjectionVulnerability/LEVEL_1?x=1")]},
        base_path="/VulnerableApp",
    )
    assert findings[0]["url"] == "/ErrorBasedSQLInjectionVulnerability/LEVEL_1"


def test_url_only_finding_is_rejected_and_counted() -> None:
    findings, summary = build_findings(
        {"findings": [_finding(cwe_ids=[], vulnerability_types=[])]},
        base_path="/VulnerableApp",
    )
    assert findings == []
    assert summary.rejected_unclassified == 1


def test_unresolved_finding_is_rejected_and_counted() -> None:
    findings, summary = build_findings(
        {"findings": [_finding(canonical_path=None, correlation_status="unresolved")]},
        base_path="/VulnerableApp",
    )
    assert findings == []
    assert summary.rejected_unresolved_operation == 1


def test_ambiguous_finding_is_rejected() -> None:
    findings, summary = build_findings(
        {"findings": [_finding(correlation_status="ambiguous")]},
        base_path="/VulnerableApp",
    )
    assert findings == []
    assert summary.rejected_unresolved_operation == 1


def test_exact_duplicates_collapse() -> None:
    findings, summary = build_findings(
        {"findings": [_finding(), _finding()]},
        base_path="/VulnerableApp",
    )
    assert len(findings) == 1
    assert summary.deduplicated == 1


def test_legacy_engine_classification_fallback_when_normalized_fields_empty() -> None:
    findings, _ = build_findings(
        {
            "findings": [
                _finding(
                    cwe_ids=[],
                    vulnerability_types=[],
                    metadata={"classification": {"cwe-id": ["CWE-89"]}},
                )
            ]
        },
        base_path="/VulnerableApp",
    )
    assert findings == [{"url": "/ErrorBasedSQLInjectionVulnerability/LEVEL_1", "cwe": "CWE-89"}]
