"""Tests for trivy_scanner.py's container-image location plumbing.

_collect_image_locations() resolves CONTAINER_IMAGE nodes to their Dockerfile
location(s) (via container_images.resolve_container_images), and
_parse_trivy_output() attaches that image ref/location data onto each
vulnerability finding it builds for an image scan.
"""

from __future__ import annotations

from nuguard.analysis.plugins.trivy_scanner import (
    _collect_image_locations,
    _parse_trivy_output,
)


def test_collect_image_locations_resolves_dockerfile_evidence() -> None:
    sbom = {
        "nodes": [
            {
                "component_type": "CONTAINER_IMAGE",
                "metadata": {"image_name": "python", "image_tag": "3.11-slim"},
                "evidence": [
                    {"kind": "dockerfile", "location": {"path": "Dockerfile", "line": 1}},
                ],
            },
            {"component_type": "AGENT", "metadata": {}},
        ]
    }
    assert _collect_image_locations(sbom) == {"python:3.11-slim": ["Dockerfile:1"]}


def test_parse_trivy_output_attaches_image_ref_and_locations_for_image_scan() -> None:
    data = {
        "Results": [{
            "Target": "python:3.11-slim",
            "Vulnerabilities": [{
                "VulnerabilityID": "CVE-2024-1",
                "PkgName": "libssl3",
                "InstalledVersion": "3.5.6",
                "FixedVersion": "3.5.8",
                "Severity": "HIGH",
            }],
        }]
    }
    findings = _parse_trivy_output(
        data, "python:3.11-slim", image_locations=["Dockerfile:1"]
    )
    assert len(findings) == 1
    assert findings[0]["container_image"] == "python:3.11-slim"
    assert findings[0]["container_image_locations"] == ["Dockerfile:1"]


def test_parse_trivy_output_omits_image_fields_for_non_image_scans() -> None:
    # fs/sbom scans pass image_locations=None (the default) — no image data
    # should be attached since there's no image association.
    data = {
        "Results": [{
            "Target": "requirements.txt",
            "Vulnerabilities": [{
                "VulnerabilityID": "CVE-2024-2",
                "PkgName": "requests",
                "InstalledVersion": "2.0.0",
                "Severity": "MEDIUM",
            }],
        }]
    }
    findings = _parse_trivy_output(data, "requirements.txt")
    assert "container_image" not in findings[0]
    assert "container_image_locations" not in findings[0]
