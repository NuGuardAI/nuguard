"""Tests for the concurrent per-image scan fan-out in grype_client.

query_grype_images() used to scan container images one at a time; it now runs
them through a small thread pool. These tests verify the fan-out still
collects every image's results and doesn't drop or duplicate findings.
"""

from __future__ import annotations

from unittest.mock import patch

from nuguard.analysis import grype_client


def _node(name: str, tag: str) -> dict:
    return {
        "component_type": "CONTAINER_IMAGE",
        "metadata": {"image_name": name, "image_tag": tag},
    }


def test_query_grype_images_collects_all_images_concurrently() -> None:
    nodes = [_node("alpine", "3.19"), _node("nginx", "1.25"), _node("curl", "8.5")]

    def _fake_run_grype(target: str, timeout: float, max_retries: int):
        # One synthetic match per image, tagged by target so we can verify
        # every image's result made it into the final list.
        return [{
            "vulnerability": {"id": f"CVE-FAKE-{target}", "severity": "high"},
            "artifact": {"name": target, "version": "1.0", "purl": f"pkg:generic/{target}@1.0"},
        }]

    with patch.object(grype_client, "_grype_path", return_value="/usr/bin/grype"), \
         patch.object(grype_client, "_run_grype", side_effect=_fake_run_grype):
        findings = grype_client.query_grype_images(nodes, timeout=5.0, max_retries=1)

    scan_targets = {f["scan_target"] for f in findings}
    assert scan_targets == {"alpine:3.19", "nginx:1.25", "curl:8.5"}
    assert len(findings) == 3


def test_query_grype_images_returns_empty_when_no_image_refs() -> None:
    with patch.object(grype_client, "_grype_path", return_value="/usr/bin/grype"):
        assert grype_client.query_grype_images([], timeout=5.0, max_retries=1) == []
