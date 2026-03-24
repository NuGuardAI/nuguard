"""Minimal CycloneDX BOM builder for analysis plugins.

Converts the raw ``nuguard.sbom.models.AiSbomDocument`` dict (as produced by
``AiSbomDocument.model_dump()``) into a CycloneDX 1.4 JSON BOM that Grype and
Trivy can consume.
"""

from __future__ import annotations

import datetime
from typing import Any


def sbom_dict_to_cyclonedx(sbom: dict[str, Any]) -> dict[str, Any]:
    """Return a CycloneDX 1.4 BOM dict from a serialised AI-SBOM dict.

    Only package dependencies (``sbom["deps"]``) are included as ``library``
    components; SBOM graph nodes are omitted since Grype/Trivy only need
    package coordinates to scan for CVEs.

    Returns an empty-component BOM (still parseable by Grype/Trivy) when the
    SBOM has no ``deps``.
    """
    components: list[dict[str, Any]] = []

    for dep in sbom.get("deps") or []:
        name    = dep.get("name") or ""
        purl    = dep.get("purl") or ""
        version = _infer_version(dep)
        if not name:
            continue
        entry: dict[str, Any] = {
            "type":   "library",
            "name":   name,
        }
        if version:
            entry["version"] = version
        if purl:
            entry["bom-ref"] = purl
            entry["purl"]    = purl
        components.append(entry)

    target    = sbom.get("target") or "unknown"
    generated = str(sbom.get("generated_at") or datetime.datetime.now(datetime.timezone.utc).isoformat())

    return {
        "bomFormat":    "CycloneDX",
        "specVersion":  "1.4",
        "version":      1,
        "metadata": {
            "timestamp": generated,
            "component": {"type": "application", "name": target},
        },
        "components": components,
    }


def _infer_version(dep: dict[str, Any]) -> str:
    """Extract the best version string from a PackageDep dict."""
    # version_spec examples: "==1.2.3", ">=1.0,<2.0", "^15.2.4"
    spec = dep.get("version_spec") or ""
    if spec.startswith("=="):
        return spec[2:]
    # purl example: pkg:pypi/fastapi@0.65.2
    purl = dep.get("purl") or ""
    if "@" in purl:
        return purl.rsplit("@", 1)[-1]
    # fall back to bare spec (e.g. "^15.2.4") — grype will still try
    return spec
