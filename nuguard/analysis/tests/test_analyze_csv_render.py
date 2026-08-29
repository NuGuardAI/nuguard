"""Unit tests for the CSV renderer (`nuguard analyze --format csv`).

The CSV output should follow the markdown report's grouping: one row per
component group (deduped across scanners and sibling components), with a
common column set — finding id, severity, title, summary, remediation,
affected components, source, framework mappings.
"""

from __future__ import annotations

import csv
import io

from nuguard.cli.commands.analyze import _render_csv
from nuguard.models.finding import Finding, Severity


def _finding(
    finding_id: str,
    rule_id: str,
    *,
    title: str = "t",
    severity: Severity = Severity.HIGH,
    description: str = "d",
    remediation: str | None = None,
    affected_component: str | None = None,
) -> Finding:
    del rule_id  # kept for call-site readability; not a Finding field
    return Finding(
        finding_id=finding_id,
        title=title,
        severity=severity,
        description=description,
        remediation=remediation,
        affected_component=affected_component,
    )


def _rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_render_csv_header_matches_common_field_set() -> None:
    text = _render_csv([])
    header = next(csv.reader(io.StringIO(text)))
    assert header == [
        "finding_id", "severity", "title", "summary", "remediation",
        "affected_components", "source", "framework_mappings",
    ]


def test_render_csv_empty_findings_has_only_header() -> None:
    text = _render_csv([])
    assert len(_rows(text)) == 0


def test_render_csv_groups_cve_findings_into_one_row_per_component() -> None:
    findings = [
        _finding(
            "grype-CVE-2024-2398", "grype-CVE-2024-2398",
            title="Known vulnerability in curl (CVE-2024-2398)",
            remediation="Upgrade curl to a version outside the affected range "
            "(fix available: 8.9.0). See https://x for details.",
            affected_component="pkg:apk/alpine/curl@8.5.0-r0",
        ),
        _finding(
            "osv-CVE-2024-9999", "osv-CVE-2024-9999",
            title="Known vulnerability in curl (CVE-2024-9999)",
            remediation="Upgrade to a patched version.",
            affected_component="pkg:apk/alpine/curl@8.5.0-r0",
        ),
    ]
    rows = _rows(_render_csv(findings))
    assert len(rows) == 1
    row = rows[0]
    assert row["finding_id"].startswith("grp-")
    assert "curl" in row["affected_components"]
    assert "CVE-2024-2398" in row["summary"]
    assert "CVE-2024-9999" in row["summary"]
    assert "8.9.0" in row["remediation"]


def test_render_csv_struct_finding_uses_its_own_finding_id() -> None:
    findings = [
        _finding(
            "nga-NGA-006-aacb1fe0", "NGA-006",
            title="Missing authentication",
            description="No auth required on this endpoint.",
            remediation="Add authentication middleware.",
            affected_component="checkout-agent",
        ),
    ]
    rows = _rows(_render_csv(findings))
    assert len(rows) == 1
    row = rows[0]
    assert row["finding_id"] == "nga-NGA-006-aacb1fe0"
    assert row["summary"] == "No auth required on this endpoint."
    assert row["remediation"] == "Add authentication middleware."
    assert row["affected_components"] == "checkout-agent"


def test_render_csv_multivalue_fields_are_semicolon_separated() -> None:
    findings = [
        _finding(
            "grype-CVE-2024-2398", "grype-CVE-2024-2398",
            affected_component="pkg:apk/alpine/curl@8.5.0-r0",
            remediation="Upgrade curl to a version outside the affected range "
            "(fix available: 8.9.0). See https://x for details.",
        ),
        _finding(
            "trivy-CVE-2024-2398", "trivy-CVE-2024-2398",
            affected_component="pkg:apk/alpine/curl@8.5.0-r0",
            remediation="Upgrade curl to a version outside the affected range "
            "(fix available: 8.9.0). See https://x for details.",
        ),
    ]
    rows = _rows(_render_csv(findings))
    assert len(rows) == 1
    assert ";" in rows[0]["source"] or "," not in rows[0]["source"]
