"""Trivy container and filesystem vulnerability scanner plugin for nuguard.

Wraps the ``trivy`` CLI binary to scan:

- Container images referenced in SBOM ``CONTAINER_IMAGE`` nodes
- Filesystem paths from SBOM nodes (``--scanners vuln,secret,misconfig``)
- **SBOM-based fallback**: when no fs/image targets are found, converts the
  nuguard SBOM to CycloneDX and runs ``trivy sbom <file>`` for dependency
  vulnerability scanning — no source code access required.

Scan mode selection (in priority order):
1. ``trivy image <ref>`` — for each CONTAINER_IMAGE node with an image ref
2. ``trivy fs <path>`` — for each filesystem path found in node metadata
3. ``trivy sbom <cdx.json>`` — CycloneDX fallback when no fs/image targets exist

Trivy output is parsed from the JSON report format into the standard nuguard
finding dict shape (matching the Grype client's output schema).

The plugin is silently skipped (returns status ``"skipped"``) when:
- ``trivy`` is not installed / not on PATH
- SBOM has no package components and no fs/image targets
- ``trivy`` exits with an unexpected error

Usage
-----
::

    from nuguard.analysis.plugins.trivy_scanner import TrivyScannerPlugin
    result = TrivyScannerPlugin().run(sbom_dict, config={})
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin

_log = logging.getLogger("analysis.plugins.trivy")

# Trivy severity → nuguard severity label
_SEV_MAP: dict[str, str] = {
    "CRITICAL": "CRITICAL",
    "HIGH":     "HIGH",
    "MEDIUM":   "MEDIUM",
    "LOW":      "LOW",
    "UNKNOWN":  "LOW",
    "NEGLIGIBLE": "LOW",
}


def _trivy_path() -> str | None:
    return shutil.which("trivy")


class TrivyScannerPlugin(AnalysisPlugin):
    """Run Trivy vulnerability scans against container images and paths in the SBOM."""

    name = "trivy"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        """Scan container images and filesystem paths referenced in the SBOM.

        Container image references are taken from nodes with
        ``component_type == CONTAINER_IMAGE``.  Filesystem paths are taken from
        ``metadata.source_path`` on all nodes.  When no scannable targets are
        found, the plugin returns ``"skipped"``.
        """
        binary = _trivy_path()
        if binary is None:
            _log.debug(
                "trivy not found on PATH; install from https://aquasecurity.github.io/trivy "
                "to enable Trivy scanning"
            )
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="trivy not installed — scan skipped",
            )

        image_refs = _collect_image_refs(sbom)
        fs_paths   = _collect_fs_paths(sbom, config)

        timeout = float(config.get("trivy_timeout", 120.0))
        all_findings: list[dict[str, Any]] = []
        scan_mode = "fs"

        if not image_refs and not fs_paths:
            # No filesystem targets — fall back to SBOM-based vulnerability scan
            _log.info("trivy: no fs/image targets found; falling back to SBOM-based scan")
            sbom_findings = _run_trivy_sbom(binary, sbom, timeout)
            if sbom_findings is None:
                return AnalysisResult(
                    status="skipped",
                    plugin=self.name,
                    message="no scannable targets found in SBOM — trivy scan skipped",
                )
            all_findings = sbom_findings
            scan_mode = "sbom"
        else:
            for ref in sorted(image_refs):
                _log.info("trivy: scanning image %s", ref)
                all_findings.extend(_run_trivy(binary, ref, "image", timeout))

            for path in sorted(fs_paths):
                _log.info("trivy: scanning path %s", path)
                all_findings.extend(_run_trivy(binary, path, "fs", timeout))

        status = "warning" if all_findings else "ok"
        if scan_mode == "sbom":
            message = (
                f"{len(all_findings)} finding(s) from SBOM dependency scan"
                if all_findings
                else "No vulnerabilities found (SBOM dependency scan)"
            )
        else:
            message = (
                f"{len(all_findings)} finding(s) across "
                f"{len(image_refs)} image(s) and {len(fs_paths)} path(s)"
                if all_findings
                else "No vulnerabilities found"
            )
        _log.info("trivy: %s", message)
        return AnalysisResult(
            status=status,
            plugin=self.name,
            message=message,
            findings=all_findings,
            details={
                "total": len(all_findings),
                "scan_mode": scan_mode,
                "images_scanned": sorted(image_refs),
                "paths_scanned": sorted(fs_paths),
            },
        )


def _collect_image_refs(sbom: dict[str, Any]) -> set[str]:
    """Collect container image references from SBOM CONTAINER_IMAGE nodes."""
    refs: set[str] = set()
    for node in sbom.get("nodes") or []:
        if (node.get("component_type") or "").upper() != "CONTAINER_IMAGE":
            continue
        meta = node.get("metadata") or {}
        extras = meta.get("extras") or {}
        ref = meta.get("base_image") or extras.get("base_image")
        if not ref:
            name = meta.get("image_name") or extras.get("image_name") or ""
            tag  = meta.get("image_tag") or extras.get("image_tag") or "latest"
            if name:
                ref = f"{name}:{tag}"
        if ref:
            refs.add(str(ref))
    return refs


def _collect_fs_paths(sbom: dict[str, Any], config: dict[str, Any]) -> set[str]:
    """Collect filesystem paths from SBOM node metadata.

    Falls back to ``config["source_path"]`` when no paths are found in SBOM
    nodes so that Trivy can scan the source directory even without explicit
    path metadata on each node.
    """
    paths: set[str] = set()
    for node in sbom.get("nodes") or []:
        meta = node.get("metadata") or {}
        extras = meta.get("extras") or {}
        for key in ("source_path", "file_path", "root_path"):
            p = meta.get(key) or extras.get(key)
            if p and Path(str(p)).exists():
                paths.add(str(p))

    # Fallback to config-supplied source path; deduplicate against node paths
    sp = config.get("source_path")
    if sp and Path(str(sp)).is_dir():
        paths.add(str(sp))

    return paths


def _run_trivy_sbom(
    binary: str,
    sbom: dict[str, Any],
    timeout: float,
) -> list[dict[str, Any]] | None:
    """Convert *sbom* to CycloneDX and run ``trivy sbom <file>``.

    Returns a list of findings (possibly empty) on success, or ``None`` if the
    SBOM has no package dependencies (nothing for Trivy to scan).
    """
    # Build CycloneDX from SBOM deps
    try:
        from nuguard.sbom.models import AiSbomDocument  # noqa: PLC0415
        from nuguard.sbom.serializer import AiSbomSerializer  # noqa: PLC0415
        doc = AiSbomDocument.model_validate(sbom)
        cdx = AiSbomSerializer.to_cyclonedx(doc)
        if not cdx.get("components"):
            _log.debug("trivy sbom: no components in CycloneDX BOM — nothing to scan")
            return None
        cdx_str = json.dumps(cdx)
    except Exception as exc:
        _log.warning("trivy sbom: failed to build CycloneDX BOM: %s", exc)
        return None

    with tempfile.NamedTemporaryFile(
        suffix=".cdx.json", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(cdx_str)
        tmp_path = tmp.name

    try:
        cmd = [
            binary, "sbom",
            "--format", "json",
            "--quiet",
            tmp_path,
        ]
        _log.debug("running: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            _log.warning("trivy sbom scan timed out")
            return []
        except OSError as exc:
            _log.warning("trivy sbom process error: %s", exc)
            return []

        if result.returncode not in (0, 1):
            stderr = result.stderr.decode(errors="replace").strip()
            _log.warning(
                "trivy sbom exited %d%s",
                result.returncode,
                f": {stderr[:200]}" if stderr else "",
            )
            return []

        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("trivy sbom output parse error: %s", exc)
            return []

        findings = _parse_trivy_output(data, "sbom")
        _log.info("trivy sbom scan: %d finding(s)", len(findings))
        return findings
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _run_trivy(
    binary: str,
    target: str,
    scan_type: str,
    timeout: float,
) -> list[dict[str, Any]]:
    """Run ``trivy <scan_type> --format json <target>`` and parse output."""
    cmd = [
        binary, scan_type,
        "--format", "json",
        "--quiet",
        "--scanners", "vuln,secret,misconfig",
        target,
    ]
    _log.debug("running: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log.warning("trivy timed out scanning %s", target)
        return []
    except OSError as exc:
        _log.warning("trivy process error for %s: %s", target, exc)
        return []

    if result.returncode not in (0, 1):
        stderr = result.stderr.decode(errors="replace").strip()
        _log.warning(
            "trivy exited %d for %s%s",
            result.returncode, target,
            f": {stderr[:200]}" if stderr else "",
        )
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("trivy output parse error for %s: %s", target, exc)
        return []

    return _parse_trivy_output(data, target)


def _parse_trivy_output(data: dict[str, Any], scan_target: str) -> list[dict[str, Any]]:
    """Parse Trivy JSON output into nuguard finding dicts."""
    findings: list[dict[str, Any]] = []

    for result in (data.get("Results") or []):
        target = result.get("Target", scan_target)

        # Vulnerabilities
        for vuln in (result.get("Vulnerabilities") or []):
            vuln_id  = vuln.get("VulnerabilityID", "")
            pkg_name = vuln.get("PkgName", "")
            pkg_ver  = vuln.get("InstalledVersion", "")
            fixed    = vuln.get("FixedVersion", "")
            severity = _SEV_MAP.get(
                str(vuln.get("Severity", "UNKNOWN")).upper(), "LOW"
            )
            cve_ids: list[str] = [vuln_id] if vuln_id.startswith("CVE-") else []
            findings.append({
                "rule_id":          vuln_id,
                "title":            vuln.get("Title") or vuln_id,
                "description":      vuln.get("Description") or vuln.get("Title") or vuln_id,
                "severity":         severity,
                "dep_name":         pkg_name,
                "dep_version":      pkg_ver,
                "affected":         [f"{pkg_name}@{pkg_ver}" if pkg_name else target],
                "affected_versions": f"<{fixed}" if fixed else "see advisory",
                "cve_ids":          cve_ids,
                "url":              vuln.get("PrimaryURL") or "",
                "source":           "trivy",
                "scan_target":      scan_target,
            })

        # Misconfigurations
        for misco in (result.get("Misconfigurations") or []):
            check_id = misco.get("ID", "")
            severity = _SEV_MAP.get(
                str(misco.get("Severity", "MEDIUM")).upper(), "MEDIUM"
            )
            findings.append({
                "rule_id":     check_id,
                "title":       misco.get("Title") or check_id,
                "description": misco.get("Description") or misco.get("Title") or check_id,
                "severity":    severity,
                "affected":    [target],
                "remediation": misco.get("Resolution") or "",
                "url":         misco.get("PrimaryURL") or "",
                "source":      "trivy-misconfig",
                "scan_target": scan_target,
            })

    return findings
