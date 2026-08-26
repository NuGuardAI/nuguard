"""Unit tests for the per-component remediation block in the markdown report.

Each CVE-table component section in the ``nuguard analyze`` markdown report
should lead with a specific, actionable remediation: which manifest file (and
line, when the source tree is available) to edit and the exact version bump,
or image-rebuild guidance for OS packages that aren't declared in any
project manifest.
"""

from __future__ import annotations

from pathlib import Path

from nuguard.cli.commands.analyze import (
    _best_fix_version,
    _component_remediation_lines,
    _fixed_version_for_finding,
    _manifest_line_number,
)
from nuguard.models.finding import Finding, Severity
from nuguard.sbom.deps import PackageDep


def _finding(finding_id: str, *, remediation: str | None = None) -> Finding:
    return Finding(
        finding_id=finding_id,
        title="t",
        severity=Severity.HIGH,
        description="d",
        remediation=remediation,
    )


def test_fixed_version_for_finding_parses_remediation_text() -> None:
    f = _finding(
        "grype-CVE-2024-2398",
        remediation="Upgrade curl to a version outside the affected range "
        "(fix available: 8.9.0). See https://x for details.",
    )
    assert _fixed_version_for_finding(f) == "8.9.0"


def test_fixed_version_for_finding_none_when_no_fix_available() -> None:
    f = _finding("grype-CVE-9999-1", remediation="Upgrade to a patched version. See https://x.")
    assert _fixed_version_for_finding(f) is None


def test_best_fix_version_picks_highest_numeric_candidate() -> None:
    best, uniq = _best_fix_version(["0.0.325", "0.1.0", "0.0.325"])
    assert best == "0.1.0"
    assert uniq == ["0.0.325", "0.1.0"]


def test_manifest_line_number_finds_dependency_line(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("flask==2.0.0\nlangchain==0.0.300\nrequests==2.28.0\n")
    assert _manifest_line_number(tmp_path, "requirements.txt", "langchain") == 2


def test_manifest_line_number_none_when_file_missing(tmp_path: Path) -> None:
    assert _manifest_line_number(tmp_path, "requirements.txt", "langchain") is None


def test_component_remediation_points_at_manifest_file_and_line(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("langchain==0.0.300\n")
    dep = PackageDep(
        name="langchain",
        version_spec="==0.0.300",
        purl="pkg:pypi/langchain@0.0.300",
        group="runtime",
        source_file="requirements.txt",
    )
    findings = [
        _finding(
            "grype-CVE-2023-36281",
            remediation="Upgrade langchain to a version outside the affected range "
            "(fix available: 0.1.0). See https://x for details.",
        )
    ]
    lines = _component_remediation_lines(
        "langchain@0.0.300", findings, {"langchain": dep}, tmp_path
    )
    text = "\n".join(lines)
    assert "requirements.txt" in text
    assert "line 1" in text
    assert "0.0.300" in text
    assert ">=0.1.0" in text


def test_component_remediation_falls_back_to_image_rebuild_for_os_packages() -> None:
    findings = [
        _finding(
            "grype-CVE-2024-2398",
            remediation="Upgrade curl to a version outside the affected range "
            "(fix available: 8.9.0). See https://x for details.",
        )
    ]
    lines = _component_remediation_lines("curl@8.5.0-r0", findings, {}, None)
    text = "\n".join(lines)
    assert "container" in text
    assert "8.9.0" in text
    assert "requirements.txt" not in text


def test_component_remediation_notes_when_no_fix_published() -> None:
    findings = [_finding("grype-CVE-9999-1", remediation="Upgrade to a patched version.")]
    lines = _component_remediation_lines("foo@1.0.0", findings, {}, None)
    assert "No fixed version" in "\n".join(lines)
