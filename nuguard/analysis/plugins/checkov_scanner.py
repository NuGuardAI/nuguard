"""Checkov IaC misconfiguration scanner plugin for nuguard.

Wraps the ``checkov`` CLI binary to scan Terraform, Kubernetes, Helm,
CloudFormation, and other IaC files referenced in the SBOM.  Checkov output
is parsed into the standard ``AnalysisResult`` finding shape.

The plugin is silently skipped (returns status ``"skipped"``) when:
- ``checkov`` is not installed / not on PATH
- No INFRASTRUCTURE_AS_CODE nodes are found in the SBOM
- ``checkov`` exits with an unexpected error

Usage
-----
::

    from nuguard.analysis.plugins.checkov_scanner import CheckovScannerPlugin
    result = CheckovScannerPlugin().run(sbom_dict, config={})
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin

_log = logging.getLogger("analysis.plugins.checkov")

# Checkov severity → nuguard severity label
_SEV_MAP: dict[str, str] = {
    "CRITICAL": "CRITICAL",
    "HIGH":     "HIGH",
    "MEDIUM":   "MEDIUM",
    "LOW":      "LOW",
    "UNKNOWN":  "LOW",
}


def _checkov_path() -> str | None:
    return shutil.which("checkov")


class CheckovScannerPlugin(AnalysisPlugin):
    """Run Checkov IaC misconfiguration checks against paths in the SBOM."""

    name = "checkov"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        """Scan IaC paths derived from the SBOM nodes.

        The plugin looks for nodes with ``component_type`` in
        ``INFRASTRUCTURE_AS_CODE``, ``DEPLOYMENT``, or ``CONFIG`` and
        collects the filesystem paths from ``metadata.source_path`` or
        ``metadata.extras.source_path``.  When no paths are found, the
        plugin returns ``"skipped"``.
        """
        binary = _checkov_path()
        if binary is None:
            _log.debug(
                "checkov not found on PATH; install from https://www.checkov.io "
                "to enable IaC scanning"
            )
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="checkov not installed — IaC scan skipped",
            )

        iac_paths = _collect_iac_paths(sbom, config)

        if not iac_paths:
            _log.debug("checkov: no IaC paths found in SBOM nodes or source_path")
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="no IaC paths found in SBOM — checkov scan skipped",
            )

        all_findings: list[dict[str, Any]] = []
        for path in iac_paths:
            all_findings.extend(_run_checkov(binary, path, config))

        status = "warning" if all_findings else "ok"
        message = (
            f"{len(all_findings)} IaC misconfiguration(s) found"
            if all_findings
            else "No IaC misconfigurations found"
        )
        _log.info("checkov: %s", message)
        return AnalysisResult(
            status=status,
            plugin=self.name,
            message=message,
            findings=all_findings,
            details={"total": len(all_findings), "scanned_paths": list(iac_paths)},
        )


def _collect_iac_paths(sbom: dict[str, Any], config: dict[str, Any]) -> set[str]:
    """Extract IaC source paths from SBOM nodes.

    Resolution order for each candidate path string *p*:
    1. Use *p* as-is when it exists on disk.
    2. Try resolving *p* relative to ``config["source_path"]``.
    3. Try resolving *p* relative to the SBOM ``target`` field (when it is a
       local directory, not a git URL).

    Falls back to the first existing directory among ``config["source_path"]``
    and the SBOM ``target`` field when no typed IaC nodes yield a resolvable
    path — Checkov will then auto-discover IaC files within that directory.
    """
    iac_types = {"INFRASTRUCTURE_AS_CODE", "DEPLOYMENT", "CONFIG"}

    # Candidate root directories for relative-path resolution, in priority order
    roots: list[Path] = []
    if sp := config.get("source_path"):
        candidate = Path(str(sp))
        if candidate.is_dir():
            roots.append(candidate)
    target = sbom.get("target") or ""
    if target and not str(target).startswith(("http://", "https://", "git@")):
        candidate = Path(str(target))
        if candidate.is_dir():
            roots.append(candidate)

    def _resolve(raw: str) -> str | None:
        """Return the first resolvable path for *raw*, or None."""
        p = Path(raw)
        if p.exists():
            return str(p)
        for root in roots:
            resolved = root / p
            if resolved.exists():
                return str(resolved)
        return None

    paths: set[str] = set()
    for node in sbom.get("nodes") or []:
        ctype = (node.get("component_type") or "").upper()
        if ctype not in iac_types:
            continue
        meta = node.get("metadata") or {}
        extras = meta.get("extras") or {}
        for key in ("source_path", "config_path", "file_path"):
            raw = meta.get(key) or extras.get(key)
            if raw:
                resolved = _resolve(str(raw))
                if resolved:
                    paths.add(resolved)

    # Fallback: scan the whole source directory when no typed IaC nodes exist
    if not paths:
        for root in roots:
            paths.add(str(root))
            break  # one root is enough for directory-wide discovery

    return paths


def _run_checkov(binary: str, path: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Run checkov against *path* and return parsed findings."""
    timeout = float(config.get("checkov_timeout", 120.0))
    cmd = [binary, "-d", path, "--output", "json", "--quiet"]
    _log.debug("running: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log.warning("checkov timed out scanning %s", path)
        return []
    except OSError as exc:
        _log.warning("checkov process error for %s: %s", path, exc)
        return []

    # Checkov exits 1 when failures are found — that's expected
    if result.returncode not in (0, 1):
        stderr = result.stderr.decode(errors="replace").strip()
        _log.warning(
            "checkov exited %d for %s%s",
            result.returncode, path,
            f": {stderr[:200]}" if stderr else "",
        )
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("checkov output parse error for %s: %s", path, exc)
        return []

    return _parse_checkov_output(data, path)


def _parse_checkov_output(data: Any, scan_path: str) -> list[dict[str, Any]]:
    """Parse checkov JSON output into nuguard finding dicts."""
    findings: list[dict[str, Any]] = []

    # checkov output may be a list (one entry per check type) or a single dict
    entries = data if isinstance(data, list) else [data]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for check_result in (entry.get("results", {}).get("failed_checks") or []):
            check_id  = check_result.get("check_id", "")
            check_obj = check_result.get("check", {})
            name      = check_obj.get("name", check_id)
            resource  = check_result.get("resource", "")
            file_path = check_result.get("file_path", scan_path)
            guideline = check_obj.get("guideline", "")
            severity  = _SEV_MAP.get(
                str(check_result.get("severity") or check_obj.get("severity") or "MEDIUM").upper(),
                "MEDIUM",
            )

            findings.append({
                "rule_id":     check_id,
                "title":       name,
                "description": f"IaC misconfiguration: {name} in `{resource}`",
                "severity":    severity,
                "affected":    [resource] if resource else [file_path],
                "remediation": guideline,
                "url":         guideline,
                "source":      "checkov",
                "scan_target": scan_path,
            })

    return findings
