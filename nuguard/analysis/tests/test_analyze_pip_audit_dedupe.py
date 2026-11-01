"""Tests for pip-audit's de-duplication with OSV/Trivy and the Recommended Actions section."""

from __future__ import annotations

from pathlib import Path

from nuguard.cli.commands.analyze import (
    _dedup_component_findings,
    _render_markdown,
    _source_tool,
)
from nuguard.models.finding import Finding, Severity

_CHROMADB = "chromadb@1.5.9"


def _f(
    finding_id: str,
    title: str,
    severity: Severity,
    component: str = _CHROMADB,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        title=title,
        severity=severity,
        description="",
        affected_component=component,
    )


def _chromadb_findings() -> list[Finding]:
    """The same chromadb advisory as reported by OSV, pip-audit and Trivy.

    OSV reports it under PYSEC, Trivy under CVE. pip-audit's title lists both,
    so it bridges the two and all three must collapse into one entry.
    """
    return [
        _f("osv-PYSEC-2026-3813-aaaa1111", "chromadb: PYSEC-2026-3813", Severity.CRITICAL),
        _f(
            "pip-audit-PYSEC-2026-3813-bbbb2222",
            "chromadb: PYSEC-2026-3813 [PYSEC-2026-3813, CVE-2026-45830]",
            Severity.MEDIUM,
        ),
        _f("trivy-CVE-2026-45830-cccc3333", "chromadb: CVE-2026-45830", Severity.HIGH),
    ]


def test_source_tool_maps_pip_audit_ids_to_pip_audit() -> None:
    assert _source_tool(_f("pip-audit-PYSEC-2026-3813-bbbb2222", "", Severity.LOW)) == "pip-audit"
    assert _source_tool(_f("osv-PYSEC-2026-3813-aaaa", "", Severity.LOW)) == "osv"


def test_pip_audit_merges_with_osv_and_trivy_into_one_group() -> None:
    deduped = _dedup_component_findings(_chromadb_findings())

    assert len(deduped) == 1
    canonical, sources = deduped[0]
    assert sources == ["osv", "pip-audit", "trivy"]
    assert canonical.finding_id.startswith("osv-")


def test_worst_severity_comes_from_rated_scanners_not_assumed_medium() -> None:
    canonical, _ = _dedup_component_findings(_chromadb_findings())[0]
    # OSV rates CRITICAL and Trivy HIGH; pip-audit's assumed MEDIUM must not win.
    assert canonical.severity == Severity.CRITICAL


def test_pip_audit_alone_keeps_assumed_medium() -> None:
    lone = [_f("pip-audit-PYSEC-2026-311-dddd", "x [PYSEC-2026-311]", Severity.MEDIUM)]
    canonical, sources = _dedup_component_findings(lone)[0]
    assert sources == ["pip-audit"]
    assert canonical.severity == Severity.MEDIUM


def test_unrelated_advisories_stay_separate() -> None:
    findings = [
        _f("osv-GHSA-aaaa-bbbb-cccc", "requests: GHSA-aaaa-bbbb-cccc", Severity.HIGH, "requests@2.0"),
        _f("pip-audit-PYSEC-2026-311-dddd", "chromadb [PYSEC-2026-311]", Severity.MEDIUM),
    ]
    assert len(_dedup_component_findings(findings)) == 2


def test_recommended_actions_lists_one_bullet_per_component_group() -> None:
    markdown = _render_markdown(
        _chromadb_findings(),
        sbom_path=Path("nuguard.sbom.json"),
        min_severity="info",
    )

    assert "## Recommended Actions" in markdown
    section = markdown.split("## Recommended Actions", 1)[1].split("\n## ", 1)[0]
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(bullets) == 1
    assert "1 advisory" in bullets[0]
    assert "CRITICAL" in bullets[0]
    assert "pip-audit" in bullets[0] and "osv" in bullets[0].lower()


def test_recommended_actions_absent_when_no_findings() -> None:
    markdown = _render_markdown([], sbom_path=Path("s.json"), min_severity="info")
    assert "## Recommended Actions" not in markdown
