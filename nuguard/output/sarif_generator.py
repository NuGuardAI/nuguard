"""Multi-run SARIF 2.1.0 generator for nuguard static analysis findings.

Produces a single ``findings.sarif`` that consolidates output from all
nuguard analysis tools (NGA rules, Checkov, Trivy, Semgrep, ATLAS) into
separate SARIF ``runs``, one per tool.  The resulting file is consumable by
GitHub Code Scanning and the VS Code SARIF Viewer.

Usage
-----
::

    from nuguard.output.sarif_generator import generate_sarif
    from nuguard.models.finding import Finding

    sarif_str = generate_sarif(findings, sbom_path="./sbom.json")
    Path("findings.sarif").write_text(sarif_str)

The ``findings`` list may contain ``Finding`` objects (from ``StaticAnalyzer``)
or raw finding dicts (from individual plugins).  Both shapes are handled.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Source-tag → SARIF tool driver name
_SOURCE_DRIVER: dict[str, str] = {
    "nga":          "nuguard-nga",
    "nga-rules":    "nuguard-nga",
    "atlas":        "nuguard-atlas",
    "atlas-native": "nuguard-atlas",
    "osv":          "nuguard-osv",
    "grype":        "nuguard-grype",
    "checkov":      "checkov",
    "trivy":        "trivy",
    "trivy-misconfig": "trivy",
    "semgrep":      "semgrep",
}

# nuguard severity → SARIF level
_SEV_TO_SARIF: dict[str, str] = {
    "critical": "error",
    "high":     "error",
    "medium":   "warning",
    "low":      "note",
    "info":     "none",
    "unknown":  "warning",
}

_TOOL_INFO: dict[str, dict[str, str]] = {
    "nuguard-nga": {
        "name": "nuguard-nga",
        "fullName": "NuGuard AI Structural Rules",
        "informationUri": "https://github.com/anthropics/nuguard",
        "version": "1.0.0",
    },
    "nuguard-atlas": {
        "name": "nuguard-atlas",
        "fullName": "NuGuard MITRE ATLAS Annotation",
        "informationUri": "https://atlas.mitre.org",
        "version": "1.0.0",
    },
    "nuguard-osv": {
        "name": "nuguard-osv",
        "fullName": "NuGuard OSV Dependency Scanner",
        "informationUri": "https://osv.dev",
        "version": "1.0.0",
    },
    "nuguard-grype": {
        "name": "nuguard-grype",
        "fullName": "NuGuard Grype CVE Scanner",
        "informationUri": "https://github.com/anchore/grype",
        "version": "1.0.0",
    },
    "checkov": {
        "name": "checkov",
        "fullName": "Checkov IaC Security Scanner",
        "informationUri": "https://www.checkov.io",
        "version": "3.x",
    },
    "trivy": {
        "name": "trivy",
        "fullName": "Trivy Vulnerability Scanner",
        "informationUri": "https://aquasecurity.github.io/trivy",
        "version": "0.x",
    },
    "semgrep": {
        "name": "semgrep",
        "fullName": "Semgrep Static Analysis",
        "informationUri": "https://semgrep.dev",
        "version": "1.x",
    },
}


def _normalize(finding: Any) -> dict[str, Any]:
    """Normalise a Finding object or raw dict to a common dict shape."""
    if hasattr(finding, "model_dump"):
        d = finding.model_dump()
        # Convert enum values to strings
        d["severity"] = str(d.get("severity", "low")).split(".")[-1].lower()
        return d
    return dict(finding)


def _driver_for(norm: dict[str, Any]) -> str:
    source = str(norm.get("source") or "nga").lower()
    return _SOURCE_DRIVER.get(source, "nuguard-nga")


def generate_sarif(
    findings: list[Any],
    sbom_path: str | Path = "sbom.json",
    scan_target: str | Path = ".",
) -> str:
    """Generate a multi-run SARIF 2.1.0 document from *findings*.

    Parameters
    ----------
    findings:
        List of ``Finding`` objects or raw finding dicts.
    sbom_path:
        Path to the source SBOM file — used as the ``artifactLocation.uri``
        for findings that have no more specific file location.
    scan_target:
        Root directory of the scan target — used for relative path resolution.

    Returns
    -------
    str
        JSON-serialised SARIF 2.1.0 document.
    """
    sbom_uri = str(sbom_path)

    # Group findings by driver name
    by_driver: dict[str, list[dict[str, Any]]] = {}
    for raw in findings:
        norm = _normalize(raw)
        driver = _driver_for(norm)
        by_driver.setdefault(driver, []).append(norm)

    runs: list[dict[str, Any]] = []
    for driver_name, driver_findings in sorted(by_driver.items()):
        runs.append(_build_run(driver_name, driver_findings, sbom_uri))

    if not runs:
        # Empty document with the nuguard-nga run (clean scan)
        runs = [_build_run("nuguard-nga", [], sbom_uri)]

    sarif: dict[str, Any] = {
        "version": "2.1.0",
        "$schema": "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0.json",
        "runs": runs,
    }
    return json.dumps(sarif, indent=2, default=str)


def _build_run(
    driver_name: str,
    findings: list[dict[str, Any]],
    sbom_uri: str,
) -> dict[str, Any]:
    """Build a single SARIF ``run`` object for *driver_name*."""
    info = _TOOL_INFO.get(driver_name, {
        "name": driver_name,
        "fullName": driver_name,
        "informationUri": "",
        "version": "0.0.0",
    })

    # Collect unique rules
    rules_map: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for f in findings:
        rule_id = _rule_id(f, driver_name)
        if rule_id not in rules_map:
            rules_map[rule_id] = _build_rule(rule_id, f)
        results.append(_build_result(rule_id, f, sbom_uri))

    return {
        "tool": {
            "driver": {
                "name": info["name"],
                "fullName": info.get("fullName", info["name"]),
                "informationUri": info.get("informationUri", ""),
                "version": info.get("version", "0.0.0"),
                "rules": list(rules_map.values()),
            }
        },
        "results": results,
    }


def _rule_id(f: dict[str, Any], driver_name: str) -> str:
    """Derive a stable rule ID for a finding."""
    rid = f.get("rule_id") or f.get("finding_id") or f.get("advisory_id") or "UNKNOWN"
    # Strip uuid suffix that StaticAnalyzer adds (e.g. "nga-NGA-001-abc12345")
    parts = str(rid).split("-")
    if len(parts) > 1 and len(parts[-1]) == 8 and parts[-1].isalnum():
        rid = "-".join(parts[:-1])
    return str(rid)


def _build_rule(rule_id: str, f: dict[str, Any]) -> dict[str, Any]:
    title = f.get("title") or f.get("summary") or rule_id
    desc  = f.get("description") or title
    refs: list[str] = f.get("references") or []
    url = f.get("url") or f.get("advisory_url") or (refs[0] if refs else "")
    return {
        "id": rule_id,
        "name": title,
        "shortDescription": {"text": str(title)[:1024]},
        "fullDescription": {"text": str(desc)[:4096]},
        "helpUri": str(url) if url else "",
        "properties": {
            "tags": [str(f.get("severity", "")).upper()],
        },
    }


def _build_result(
    rule_id: str,
    f: dict[str, Any],
    sbom_uri: str,
) -> dict[str, Any]:
    sev = str(f.get("severity", "low")).lower().split(".")[-1]
    level = _SEV_TO_SARIF.get(sev, "warning")
    msg = f.get("description") or f.get("summary") or f.get("title") or rule_id

    # Determine artifact location (file path + optional line number)
    affected = f.get("affected") or f.get("affected_component") or []
    if isinstance(affected, str):
        affected = [affected]
    location_uri = sbom_uri

    locations: list[dict[str, Any]] = []
    if affected:
        for a in affected[:3]:  # max 3 locations per result
            a_str = str(a)
            region: dict[str, Any] = {}
            # Parse "file.py:42" style
            if ":" in a_str:
                parts = a_str.rsplit(":", 1)
                if parts[1].isdigit():
                    location_uri = parts[0]
                    region = {"startLine": int(parts[1])}
                else:
                    location_uri = a_str
            else:
                location_uri = a_str if "/" in a_str or "." in a_str else sbom_uri
            loc: dict[str, Any] = {
                "physicalLocation": {
                    "artifactLocation": {"uri": location_uri},
                }
            }
            if region:
                loc["physicalLocation"]["region"] = region
            locations.append(loc)
    else:
        locations = [{"physicalLocation": {"artifactLocation": {"uri": sbom_uri}}}]

    result: dict[str, Any] = {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": str(msg)[:2048]},
        "locations": locations,
    }

    # Attach CVE IDs as related locations / properties
    cve_ids = f.get("cve_ids") or []
    if cve_ids:
        result["properties"] = {"cve_ids": cve_ids}

    return result
