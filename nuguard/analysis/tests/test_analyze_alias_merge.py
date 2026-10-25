"""Regression tests: alias-aware cross-component merging and pip-audit manifest filtering."""

from __future__ import annotations

from pathlib import Path

from nuguard.analysis.plugins.nga_rules import _osv_to_finding
from nuguard.analysis.plugins.pip_audit_scanner import _collect_manifest_targets
from nuguard.cli.commands.analyze import _compute_component_groups, _vuln_ids
from nuguard.models.finding import Finding, Severity


def _f(finding_id: str, title: str, severity: Severity, component: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        title=title,
        severity=severity,
        description="",
        affected_component=component,
    )


def test_pyproject_without_project_table_is_not_an_audit_target(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    sbom = {
        "target": str(tmp_path),
        "deps": [{"name": "x", "purl": "pkg:pypi/x", "source_file": "app/pyproject.toml"}],
    }
    assert _collect_manifest_targets(sbom, {}) == set()


def test_same_package_under_osv_and_pip_audit_labels_merges_on_shared_advisory() -> None:
    findings = [
        _f("osv-PYSEC-2026-3813-aaaa1111", "chromadb: PYSEC-2026-3813 [PYSEC-2026-3813, CVE-2026-45830]",
           Severity.CRITICAL, "chromadb"),
        _f("pip-audit-PYSEC-2026-3813-bbbb2222",
           "chromadb: PYSEC-2026-3813 [PYSEC-2026-3813, CVE-2026-45830]",
           Severity.MEDIUM, "chromadb@1.5.9"),
        _f("pip-audit-PYSEC-2026-3814-cccc3333",
           "chromadb: PYSEC-2026-3814 [PYSEC-2026-3814, CVE-2026-45833]",
           Severity.MEDIUM, "chromadb@1.5.9"),
    ]
    groups = _compute_component_groups(findings)

    assert len(groups) == 1
    group = groups[0]
    assert sorted(group.components) == ["chromadb", "chromadb@1.5.9"]
    # Shared advisory collapses to one entry; the extra pip-audit advisory is kept.
    assert len(group.entries) == 2


def test_osv_finding_title_carries_all_aliases_for_dedupe() -> None:
    osv = {
        "dep_name": "chromadb",
        "dep_version": "1.5.9",
        "purl": "pkg:pypi/chromadb@1.5.9",
        "advisory_id": "GHSA-2wm9-hf6c-p5cr",
        "aliases": ["PYSEC-2026-3813", "CVE-2026-45830"],
        "cve_ids": ["CVE-2026-45830"],
        "summary": "ChromaDB allows cross-tenant access",
        "severity": "CRITICAL",
        "affected_versions": "<=1.5.9",
        "url": "https://osv.dev/vulnerability/GHSA-2wm9-hf6c-p5cr",
    }
    finding = _osv_to_finding(osv)
    assert "GHSA-2WM9-HF6C-P5CR" in finding["title"] or "GHSA-2wm9-hf6c-p5cr" in finding["title"]
    assert "PYSEC-2026-3813" in finding["title"]
    assert "CVE-2026-45830" in finding["title"]
    assert "CVE-2026-45830" in finding["cve_ids"]


def test_osv_finding_without_aliases_keeps_plain_title() -> None:
    osv = {
        "dep_name": "requests",
        "dep_version": "2.0",
        "purl": "pkg:pypi/requests@2.0",
        "advisory_id": "GHSA-aaaa-bbbb-cccc",
        "aliases": [],
        "cve_ids": [],
        "summary": "x",
        "severity": "HIGH",
        "affected_versions": "<2.31",
        "url": "https://osv.dev/vulnerability/GHSA-aaaa-bbbb-cccc",
    }
    assert "[" not in _osv_to_finding(osv)["title"]


def test_vuln_ids_reads_aliases_from_osv_title() -> None:
    f = _f(
        "osv-GHSA-2wm9-hf6c-p5cr-aaaa1111",
        "chromadb: GHSA-2wm9-hf6c-p5cr [GHSA-2wm9-hf6c-p5cr, PYSEC-2026-3813]",
        Severity.CRITICAL,
        "chromadb",
    )
    assert {"GHSA-2WM9-HF6C-P5CR", "PYSEC-2026-3813"} <= _vuln_ids(f)


def test_remediation_names_are_unique_across_labels_of_one_package() -> None:
    from nuguard.cli.commands.analyze import _component_remediation_text

    text = _component_remediation_text(
        ["chromadb", "chromadb@1.5.9"],
        [_f("osv-GHSA-x-aaaa", "chromadb: GHSA-x [GHSA-x]", Severity.CRITICAL, "chromadb")],
        {},
        None,
    )
    assert text.count("`chromadb`") == 1
