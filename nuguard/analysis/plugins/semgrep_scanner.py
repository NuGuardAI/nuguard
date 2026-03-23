"""Semgrep static code analysis plugin for nuguard.

Runs the bundled ``ai-security.yaml`` ruleset (and any additional rules in
``config["semgrep_rules"]``) against source paths extracted from the SBOM.

The plugin is silently skipped (returns status ``"skipped"``) when:
- ``semgrep`` is not installed / not on PATH
- No source paths are found in the SBOM
- ``semgrep`` exits with an unexpected error

Usage
-----
::

    from nuguard.analysis.plugins.semgrep_scanner import SemgrepScannerPlugin
    result = SemgrepScannerPlugin().run(sbom_dict, config={})

Config keys
-----------
``semgrep_rules``
    Path to additional semgrep rules file or directory (optional).
``semgrep_timeout``
    Per-process timeout in seconds (default 120).
``semgrep_exclude_tests``
    When True (default), ``--exclude tests/`` is added to the command.
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

_log = logging.getLogger("analysis.plugins.semgrep")

# Bundled AI-security ruleset (ships with nuguard)
_BUNDLED_RULES: Path = (
    Path(__file__).parent / "semgrep_rules" / "ai-security.yaml"
)

# Semgrep severity → nuguard severity label
_SEV_MAP: dict[str, str] = {
    "ERROR":   "HIGH",
    "WARNING": "MEDIUM",
    "INFO":    "INFO",
}


def _semgrep_path() -> str | None:
    return shutil.which("semgrep")


class SemgrepScannerPlugin(AnalysisPlugin):
    """Run Semgrep static analysis with the bundled AI-security ruleset."""

    name = "semgrep"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        """Scan source paths referenced in the SBOM with Semgrep.

        Source paths are extracted from node ``metadata.source_path`` or
        ``metadata.extras.source_path``.  The bundled ``ai-security.yaml``
        ruleset is always included; additional rules can be specified via
        ``config["semgrep_rules"]``.
        """
        binary = _semgrep_path()
        if binary is None:
            _log.debug(
                "semgrep not found on PATH; install from https://semgrep.dev "
                "to enable static code analysis"
            )
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="semgrep not installed — code scan skipped",
            )

        src_paths = _collect_source_paths(sbom, config)
        if not src_paths:
            _log.debug("semgrep: no source paths found in SBOM nodes")
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="no source paths found in SBOM — semgrep scan skipped",
            )

        rule_files: list[str] = [str(_BUNDLED_RULES)]
        extra_rules = config.get("semgrep_rules")
        if extra_rules and Path(str(extra_rules)).exists():
            rule_files.append(str(extra_rules))

        all_findings: list[dict[str, Any]] = []
        timeout = float(config.get("semgrep_timeout", 120.0))
        exclude_tests = config.get("semgrep_exclude_tests", True)

        for src_path in sorted(src_paths):
            _log.info("semgrep: scanning %s", src_path)
            all_findings.extend(
                _run_semgrep(binary, src_path, rule_files, timeout, exclude_tests)
            )

        status = "warning" if all_findings else "ok"
        message = (
            f"{len(all_findings)} code pattern finding(s)"
            if all_findings
            else "No code pattern findings"
        )
        _log.info("semgrep: %s", message)
        return AnalysisResult(
            status=status,
            plugin=self.name,
            message=message,
            findings=all_findings,
            details={
                "total": len(all_findings),
                "paths_scanned": sorted(src_paths),
                "rule_files": rule_files,
            },
        )


def _collect_source_paths(sbom: dict[str, Any], config: dict[str, Any]) -> set[str]:
    """Collect source code paths from SBOM node metadata.

    Falls back to ``config["source_path"]`` when no paths are found in SBOM
    nodes so that Semgrep can still scan when path metadata is absent.
    """
    paths: set[str] = set()
    for node in sbom.get("nodes") or []:
        meta = node.get("metadata") or {}
        extras = meta.get("extras") or {}
        for key in ("source_path", "root_path", "repo_path"):
            p = meta.get(key) or extras.get(key)
            if p and Path(str(p)).exists():
                paths.add(str(p))

    # Fallback to config-supplied source path
    sp = config.get("source_path")
    if sp and Path(str(sp)).is_dir():
        paths.add(str(sp))

    return paths


def _run_semgrep(
    binary: str,
    src_path: str,
    rule_files: list[str],
    timeout: float,
    exclude_tests: bool,
) -> list[dict[str, Any]]:
    """Run semgrep and return parsed finding dicts."""
    cmd = [binary, "--json", "--quiet"]
    for rf in rule_files:
        cmd += ["--config", rf]
    if exclude_tests:
        cmd += ["--exclude", "tests/", "--exclude", "test_*.py", "--exclude", "*_test.py"]
    cmd.append(src_path)

    _log.debug("running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log.warning("semgrep timed out scanning %s", src_path)
        return []
    except OSError as exc:
        _log.warning("semgrep process error for %s: %s", src_path, exc)
        return []

    # Semgrep exits 1 when findings exist
    if result.returncode not in (0, 1):
        stderr = result.stderr.decode(errors="replace").strip()
        _log.warning(
            "semgrep exited %d for %s%s",
            result.returncode, src_path,
            f": {stderr[:200]}" if stderr else "",
        )
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("semgrep output parse error for %s: %s", src_path, exc)
        return []

    return _parse_semgrep_output(data, src_path)


def _parse_semgrep_output(data: dict[str, Any], scan_path: str) -> list[dict[str, Any]]:
    """Convert semgrep JSON results into nuguard finding dicts."""
    findings: list[dict[str, Any]] = []
    for r in (data.get("results") or []):
        check_id = r.get("check_id", "")
        msg      = r.get("extra", {}).get("message", check_id)
        severity = _SEV_MAP.get(
            str(r.get("extra", {}).get("severity") or r.get("severity", "WARNING")).upper(),
            "MEDIUM",
        )
        file_path = r.get("path", scan_path)
        start_line = r.get("start", {}).get("line")
        location   = f"{file_path}:{start_line}" if start_line else file_path
        meta = r.get("extra", {}).get("metadata") or {}
        owasp = meta.get("owasp", "")
        nga_rule = meta.get("nuguard_rule", "")

        findings.append({
            "rule_id":     check_id,
            "title":       check_id.split(".")[-1].replace("-", " ").title(),
            "description": msg,
            "severity":    severity,
            "affected":    [location],
            "remediation": owasp,
            "url":         "",
            "source":      "semgrep",
            "scan_target": scan_path,
            "nga_rule":    nga_rule,
        })
    return findings
