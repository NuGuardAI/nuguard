"""Tests for resolve_container_images() — CONTAINER_IMAGE node → image ref +
Dockerfile location(s) resolution, shared by grype_client.py and trivy_scanner.py.
"""

from __future__ import annotations

from nuguard.analysis.container_images import resolve_container_images


def _node(*, image_name=None, image_tag=None, base_image=None, evidence=None) -> dict:
    metadata: dict = {}
    if base_image:
        metadata["base_image"] = base_image
    if image_name:
        metadata["image_name"] = image_name
    if image_tag:
        metadata["image_tag"] = image_tag
    return {
        "component_type": "CONTAINER_IMAGE",
        "metadata": metadata,
        "evidence": evidence or [],
    }


def test_single_dockerfile_evidence_resolves_to_one_location() -> None:
    node = _node(
        base_image="python:3.11-slim",
        evidence=[{"kind": "dockerfile", "location": {"path": "Dockerfile", "line": 12}}],
    )
    assert resolve_container_images([node]) == {"python:3.11-slim": ["Dockerfile:12"]}


def test_multiple_dockerfile_evidence_entries_all_collected_and_deduped() -> None:
    # Mirrors the real pinnacle-bank SBOM: a shared base image referenced by
    # many microservice Dockerfiles all show up as separate evidence entries.
    node = _node(
        base_image="python:3.11-slim",
        evidence=[
            {"kind": "dockerfile", "location": {"path": "src/api/Dockerfile", "line": 1}},
            {"kind": "dockerfile", "location": {"path": "src/worker/Dockerfile", "line": 1}},
            # duplicate path should not appear twice
            {"kind": "dockerfile", "location": {"path": "src/api/Dockerfile", "line": 1}},
        ],
    )
    result = resolve_container_images([node])
    assert result["python:3.11-slim"] == ["src/api/Dockerfile:1", "src/worker/Dockerfile:1"]


def test_no_evidence_resolves_to_empty_list() -> None:
    node = _node(base_image="alpine:3.19")
    assert resolve_container_images([node]) == {"alpine:3.19": []}


def test_ref_falls_back_to_image_name_and_tag_when_no_base_image() -> None:
    node = _node(image_name="node", image_tag="20-alpine")
    assert resolve_container_images([node]) == {"node:20-alpine": []}


def test_ref_defaults_tag_to_latest_when_missing() -> None:
    node = _node(image_name="redis")
    assert resolve_container_images([node]) == {"redis:latest": []}


def test_node_with_no_resolvable_ref_is_skipped() -> None:
    node = {"component_type": "CONTAINER_IMAGE", "metadata": {}, "evidence": []}
    assert resolve_container_images([node]) == {}
