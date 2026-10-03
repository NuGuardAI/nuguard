"""CONTAINER_IMAGE detections for image references found outside a Dockerfile.

Compose, Kubernetes, Bicep, Terraform and CloudFormation workloads name the image
they run. This builds the shared ``image_role="base"`` node (OS inferred offline) and
the ``RUNS`` hint from the workload to it, so every orchestrator produces the same
image nodes — and they dedupe with a Dockerfile's ``FROM`` node for the same ref.
"""

from __future__ import annotations

from typing import Any

from ..image_os import infer_os
from ..image_ref import is_templated, parse_image_ref
from ..types import ComponentType
from .base import ComponentDetection, RelationshipHint


def image_canonical(ref: str) -> str:
    """Canonical name shared with ``DockerfileAdapter`` base-image nodes."""
    return f"container_image:{ref.strip().lower()}"


def image_detection(
    ref: str, *, adapter_name: str, file_path: str, line: int = 1, priority: int = 8
) -> ComponentDetection:
    """A ``CONTAINER_IMAGE`` detection for *ref* (a pulled, not built, image)."""
    ref = ref.strip()
    parts = parse_image_ref(ref)
    name = parts["name"] or ref
    tag = parts["tag"]
    metadata: dict[str, Any] = {
        "base_image": ref,
        "image_name": name,
        "image_tag": tag or ("" if parts["digest"] else "latest"),
        "image_digest": parts["digest"],
        "registry": parts["registry"] or "docker.io",
        "image_role": "base",
    }
    os_info = infer_os(ref)
    if os_info is not None:
        metadata.update(os_name=os_info.name, os_family=os_info.family, os_evidence=os_info.evidence)
        if os_info.version:
            metadata["os_version"] = os_info.version
    display = f"{name}:{tag}" if tag else (name if parts["digest"] else f"{name}:latest")
    return ComponentDetection(
        component_type=ComponentType.CONTAINER_IMAGE,
        canonical_name=image_canonical(ref),
        display_name=display,
        adapter_name=adapter_name,
        priority=priority,
        confidence=0.95,
        metadata=metadata,
        file_path=file_path,
        line=line,
        snippet=f"image: {ref}"[:120],
        evidence_kind="iac",
    )


def runs_hint(workload_canonical: str, image_ref: str) -> RelationshipHint:
    """``workload --RUNS--> image`` hint."""
    return RelationshipHint(
        source_canonical=workload_canonical,
        source_type=ComponentType.DEPLOYMENT,
        target_canonical=image_canonical(image_ref),
        target_type=ComponentType.CONTAINER_IMAGE,
        relationship_type="RUNS",
    )


def attach_images(
    workload: ComponentDetection,
    refs: list[str],
    *,
    adapter_name: str,
    file_path: str,
    line: int = 1,
) -> list[ComponentDetection]:
    """Add RUNS hints to *workload* for resolvable *refs*; return the image detections."""
    images: dict[str, ComponentDetection] = {}
    for ref in refs:
        if not ref or is_templated(ref):
            continue
        det = image_detection(ref, adapter_name=adapter_name, file_path=file_path, line=line)
        images.setdefault(det.canonical_name, det)
        workload.relationships.append(runs_hint(workload.canonical_name, ref))
    return list(images.values())
