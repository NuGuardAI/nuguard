"""Tests for OSV ``last_affected`` handling.

Advisories that end their range with ``last_affected`` (no ``fixed`` event)
must render as a bounded range and must not be mistaken for a fix version.
The remediation names the last affected version so readers can see which
releases are still vulnerable.
"""

from __future__ import annotations

import pytest

from nuguard.analysis.osv_client import _affected_versions
from nuguard.analysis.plugins.nga_rules import (
    _extract_fixed_version,
    _extract_last_affected,
    _osv_to_finding,
)
from nuguard.cli.commands.analyze import _component_remediation_text
from nuguard.models.finding import Finding, Severity


def _detail(*events: dict) -> dict:
    return {"affected": [{"ranges": [{"type": "ECOSYSTEM", "events": list(events)}]}]}


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([{"introduced": "0.4.17"}, {"last_affected": "1.5.9"}], ">=0.4.17,<=1.5.9"),
        ([{"introduced": "0"}, {"last_affected": "1.5.9"}], ">=0,<=1.5.9"),
        ([{"introduced": "1.0"}, {"fixed": "1.2"}], ">=1.0,<1.2"),
        ([{"introduced": "1.0"}], ">=1.0"),
    ],
)
def test_affected_versions_renders_last_affected(events: list[dict], expected: str) -> None:
    assert _affected_versions(_detail(*events)) == expected


def test_last_affected_range_has_no_fix_version() -> None:
    assert _extract_fixed_version(">=0.4.17,<=1.5.9") is None
    assert _extract_last_affected(">=0.4.17,<=1.5.9") == "1.5.9"


def test_fixed_range_still_reports_fix_version() -> None:
    assert _extract_fixed_version(">=1.0,<1.2") == "1.2"
    assert _extract_last_affected(">=1.0,<1.2") is None


def test_osv_finding_for_last_affected_advisory_has_no_fix_claim() -> None:
    finding = _osv_to_finding(
        {
            "dep_name": "chromadb",
            "dep_version": "1.5.9",
            "advisory_id": "PYSEC-2026-3813",
            "summary": "code injection",
            "severity": "CRITICAL",
            "affected_versions": ">=0.4.17,<=1.5.9",
            "purl": "pkg:pypi/chromadb@1.5.9",
        }
    )
    assert "Fixed in" not in finding["description"]
    assert "fix available" not in finding["remediation"]
    assert "up to 1.5.9;" in finding["remediation"]
    assert finding["fixed_version"] is None


def _finding(remediation: str) -> Finding:
    return Finding(
        finding_id="PYSEC-2026-3813",
        title="t",
        severity=Severity.CRITICAL,
        description="d",
        remediation=remediation,
    )


def test_component_remediation_unchanged_without_last_affected() -> None:
    findings = [_finding("Upgrade x to a version outside the affected range. See https://osv.dev.")]
    text = _component_remediation_text(["x@1.0"], findings, {}, source_root=None)
    assert "No fixed version has been published yet" in text
