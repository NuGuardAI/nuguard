"""Unit tests for the final cross-component dedup pass in the markdown report.

Sibling binary packages built from the same upstream source (e.g. curl/libcurl,
libssl3/libcrypto3) often report an identical or subset CVE set under different
component labels. `_merge_overlapping_components` folds those into one group so
the same vulnerability isn't listed twice, while keeping the per-library compact
CVE table intact within the merged group.
"""

from __future__ import annotations

from pathlib import Path

from nuguard.cli.commands.analyze import (
    _dedup_component_findings,
    _group_by_component,
    _merge_overlapping_components,
    _render_markdown,
)
from nuguard.models.finding import Finding, Severity


def _cve_finding(source: str, cve: str, comp: str, sev: Severity = Severity.HIGH) -> Finding:
    return Finding(
        finding_id=f"{source}-{cve}",
        title=f"{cve} in {comp}",
        severity=sev,
        description="d",
        affected_component=comp,
        remediation="Upgrade to a version outside the affected range "
        "(fix available: 9.0.0). See https://x for details.",
    )


def _struct_finding(comp: str, rule: str) -> Finding:
    return Finding(
        finding_id=f"nga-{rule}-aaaa1111",
        title=f"{rule} on {comp}",
        severity=Severity.HIGH,
        description="structural finding",
        affected_component=comp,
    )


def _grouped(findings: list[Finding]):
    grouped_raw = _group_by_component(findings)
    return [(comp, _dedup_component_findings(flist)) for comp, flist in grouped_raw]


def test_subset_cve_set_merges_into_superset_component() -> None:
    findings = [
        _cve_finding("grype", "CVE-2024-1", "curl@8.5.0-r0"),
        _cve_finding("grype", "CVE-2024-2", "curl@8.5.0-r0"),
        _cve_finding("grype", "CVE-2024-3", "curl@8.5.0-r0"),
        _cve_finding("trivy", "CVE-2024-1", "libcurl@8.5.0-r0"),
        _cve_finding("trivy", "CVE-2024-2", "libcurl@8.5.0-r0"),
    ]
    groups = _merge_overlapping_components(_grouped(findings))
    assert len(groups) == 1
    g = groups[0]
    assert g.components == ["curl@8.5.0-r0", "libcurl@8.5.0-r0"]
    assert len(g.entries) == 3  # still 3 unique CVEs, not 5

    sources_by_cve = {
        f.finding_id.split("-", 1)[1]: sorted(sources) for f, sources in g.entries
    }
    assert sources_by_cve["CVE-2024-1"] == ["grype", "trivy"]
    assert sources_by_cve["CVE-2024-3"] == ["grype"]


def test_disjoint_cve_sets_do_not_merge() -> None:
    findings = [
        _cve_finding("grype", "CVE-2024-1", "curl@8.5.0-r0"),
        _cve_finding("grype", "CVE-2024-9", "unrelated@1.0.0"),
    ]
    groups = _merge_overlapping_components(_grouped(findings))
    assert len(groups) == 2
    assert {g.components[0] for g in groups} == {"curl@8.5.0-r0", "unrelated@1.0.0"}


def test_structural_only_component_never_merges() -> None:
    findings = [
        _cve_finding("grype", "CVE-2024-1", "curl@8.5.0-r0"),
        _struct_finding("agent-1", "NGA-006"),
    ]
    groups = _merge_overlapping_components(_grouped(findings))
    assert len(groups) == 2


def test_structural_finding_on_a_merged_component_is_kept_as_its_own_row() -> None:
    findings = [
        _cve_finding("grype", "CVE-2024-1", "curl@8.5.0-r0"),
        _cve_finding("trivy", "CVE-2024-1", "libcurl@8.5.0-r0"),
        _struct_finding("libcurl@8.5.0-r0", "NGA-006"),
    ]
    groups = _merge_overlapping_components(_grouped(findings))
    assert len(groups) == 1
    g = groups[0]
    assert len(g.entries) == 2  # the shared CVE (merged) + the structural finding
    assert any(f.finding_id.startswith("nga-") for f, _ in g.entries)


def test_render_markdown_shows_one_section_for_merged_pair() -> None:
    findings = [
        _cve_finding("grype", "CVE-2024-1", "curl@8.5.0-r0", Severity.CRITICAL),
        _cve_finding("grype", "CVE-2024-2", "curl@8.5.0-r0"),
        _cve_finding("trivy", "CVE-2024-1", "libcurl@8.5.0-r0", Severity.CRITICAL),
    ]
    out = _render_markdown(findings, Path("fake.sbom.json"), "medium")
    assert out.count("### 🔴 `curl@8.5.0-r0`") == 1
    assert "libcurl@8.5.0-r0" not in out.split("## Findings")[0]  # not its own section
    assert "**Affected Components:** `curl@8.5.0-r0`, `libcurl@8.5.0-r0`" in out
    assert "**Total findings:** 2 unique" in out
