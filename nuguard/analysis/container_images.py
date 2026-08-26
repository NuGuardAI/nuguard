"""Resolve CONTAINER_IMAGE SBOM nodes to their scan ref and Dockerfile location(s).

Used by ``grype_client.py`` and ``trivy_scanner.py`` so container-package
findings can name the actual image and where it's declared in the repo,
instead of just the bare package name — see ``nuguard.cli.commands.analyze``'s
``_component_remediation_text`` for how this is surfaced in the report.
"""
from __future__ import annotations

from typing import Any


def resolve_container_images(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map each CONTAINER_IMAGE node's resolved image ref to its Dockerfile
    location(s), e.g. ``{"python:3.11-slim": ["src/api/Dockerfile:1", ...]}``.

    A base image can be declared in more than one Dockerfile (common with
    shared base images across microservices) — each node's ``evidence`` list
    may carry multiple ``dockerfile`` entries, one per FROM occurrence found
    during SBOM extraction. Locations are deduplicated by path; the value is
    an empty list when a node has no location evidence (e.g. a base image
    declared some other way than a Dockerfile FROM).
    """
    locations_by_ref: dict[str, list[str]] = {}
    for node in nodes:
        meta = node.get("metadata") or {}
        extras = meta.get("extras") or {}
        ref = meta.get("base_image") or extras.get("base_image")
        if not ref:
            name = meta.get("image_name") or extras.get("image_name") or ""
            tag = meta.get("image_tag") or extras.get("image_tag") or "latest"
            if name:
                ref = f"{name}:{tag}"
        if not ref:
            continue
        ref = str(ref)

        seen_paths: set[str] = set()
        locations = locations_by_ref.setdefault(ref, [])
        for existing in locations:
            seen_paths.add(existing.split(":", 1)[0])
        for ev in node.get("evidence") or []:
            loc = ev.get("location") or {}
            path = loc.get("path")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            line = loc.get("line")
            locations.append(f"{path}:{line}" if line is not None else path)

    return locations_by_ref
