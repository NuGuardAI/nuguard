"""Unit tests for cross-scanner finding dedup in ``nuguard analyze``'s markdown renderer.

Covers the PURL-normalisation bug where grype's distro-qualified OS-package PURLs
(``pkg:apk/alpine/curl@8.5.0-r0?arch=x86_64&distro=alpine-3.19.1``) failed to
group with trivy's bare ``curl@8.5.0-r0`` label for the same package, and the
severity-reconciliation bug where the arbitrarily-picked canonical finding's
severity was used instead of the worst-case severity across sources.
"""

from __future__ import annotations

from nuguard.cli.commands.analyze import (
    _component_label,
    _dedup_component_findings,
    _group_by_component,
)
from nuguard.models.finding import Finding, Severity


def _finding(
    finding_id: str,
    *,
    title: str = "",
    severity: Severity = Severity.MEDIUM,
    affected_component: str | None = None,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        title=title,
        severity=severity,
        description="",
        affected_component=affected_component,
    )


def test_component_label_normalises_grype_and_trivy_os_purls_to_same_label() -> None:
    grype_label = _component_label(
        _finding(
            "grype-CVE-2024-2398",
            affected_component="pkg:apk/alpine/curl@8.5.0-r0?arch=x86_64&distro=alpine-3.19.1",
        )
    )
    trivy_label = _component_label(
        _finding("trivy-CVE-2024-2398", affected_component="curl@8.5.0-r0")
    )
    assert grype_label == trivy_label == "curl@8.5.0-r0"


def test_component_label_strips_qualifiers_but_keeps_non_os_namespace() -> None:
    # npm scope must survive — it's part of the package identity, not a distro name
    label = _component_label(
        _finding("grype-x", affected_component="pkg:npm/@babel/core@7.0.0")
    )
    assert label == "@babel/core@7.0.0"


def test_component_label_strips_deb_namespace() -> None:
    label = _component_label(
        _finding(
            "grype-x",
            affected_component="pkg:deb/debian/perl-base@5.40.1-6?arch=amd64&distro=debian-13",
        )
    )
    assert label == "perl-base@5.40.1-6"


def test_dedup_merges_same_cve_across_sources_with_differing_severity() -> None:
    grype_finding = _finding(
        "grype-CVE-2024-2398",
        title="Known vulnerability in curl (CVE-2024-2398)",
        severity=Severity.HIGH,
        affected_component="pkg:apk/alpine/curl@8.5.0-r0?arch=x86_64&distro=alpine-3.19.1",
    )
    trivy_finding = _finding(
        "trivy-CVE-2024-2398",
        title="curl: HTTP/2 push headers memory-leak",
        severity=Severity.MEDIUM,
        affected_component="curl@8.5.0-r0",
    )

    grouped = _group_by_component([grype_finding, trivy_finding])
    assert len(grouped) == 1  # both normalise to the same component label

    _comp, flist = grouped[0]
    deduped = _dedup_component_findings(flist)

    assert len(deduped) == 1
    canonical, sources = deduped[0]
    assert sources == ["grype", "trivy"]
    # worst-case severity across sources, not whichever finding was picked as canonical
    assert canonical.severity == Severity.HIGH


def test_dedup_keeps_distinct_cves_separate() -> None:
    f1 = _finding("grype-CVE-2024-2398", affected_component="curl@8.5.0-r0")
    f2 = _finding("grype-CVE-2024-6197", affected_component="curl@8.5.0-r0")
    deduped = _dedup_component_findings([f1, f2])
    assert len(deduped) == 2
