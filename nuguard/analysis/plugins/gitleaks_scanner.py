"""Hardcoded-secret scanner plugin for nuguard, backed by gitleaks.

Runs ``gitleaks dir`` against the source paths recorded in the SBOM. Gitleaks
is an MIT-licensed scanner with a large built-in rule set for cloud, SaaS,
and generic credentials. It is complementary to the Opengrep rules, which
focus on code patterns rather than secret formats.

The plugin is skipped (returns status ``"skipped"``) when:
- ``gitleaks`` is not on PATH
- No source paths are found in the SBOM

Execution and output errors are reported as incomplete scans.

Secret values are never reported. Gitleaks runs with ``--redact``, and the
parser keeps only the rule, location, and description of each leak.

Gitleaks does not report a severity, so every finding is rated HIGH. A
committed credential should be rotated regardless of its type.

Leaks in files under a directory named ``test`` or ``tests`` (relative to the
scanned source root) are excluded by default, because test fixtures often
carry intentionally fake credentials.

Usage
-----
::

    from nuguard.analysis.plugins.gitleaks_scanner import GitleaksScannerPlugin
    result = GitleaksScannerPlugin().run(sbom_dict, config={})

Config keys
-----------
``gitleaks_timeout``
    Per-process timeout in seconds (default 120).
``gitleaks_total_timeout``
    Total budget across all source paths in seconds (default 300).
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin
from nuguard.analysis.plugins.scanner_runtime import collect_sbom_source_paths, run_scanner
from nuguard.common.logging import get_logger

_log = get_logger("analysis.plugins.gitleaks")

_BINARY = "gitleaks"
_SEVERITY = "HIGH"
# Directory names whose contents are excluded from gitleaks results by default.
# Test fixtures often contain intentionally fake credentials.
_EXCLUDED_DIR_NAMES = frozenset({"test", "tests"})


class GitleaksScannerPlugin(AnalysisPlugin):
    """Scan SBOM source paths for hardcoded secrets with gitleaks."""

    name = "gitleaks"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        """Scan every source path referenced in the SBOM for leaked credentials."""
        binary = shutil.which(_BINARY)
        if binary is None:
            _log.info(
                "gitleaks not found on PATH; install from https://github.com/gitleaks/gitleaks "
                "to enable secret scanning"
            )
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="gitleaks not installed — secret scan skipped",
            )

        src_paths = collect_sbom_source_paths(sbom, config)
        if not src_paths:
            _log.info("gitleaks: no source paths found in SBOM nodes")
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="no source paths found in SBOM — gitleaks scan skipped",
            )

        result = run_scanner(
            self.name,
            src_paths,
            lambda path: _gitleaks_command(binary, path),
            _parse_gitleaks_output,
            timeout=config.get("gitleaks_timeout", 120.0),
            total_timeout=config.get("gitleaks_total_timeout", 300.0),
        )
        result.details["paths_scanned"] = result.details.get("scanned_paths", [])
        return result


def _gitleaks_command(binary: str, src_path: str) -> list[str]:
    """Build a ``gitleaks dir`` command that writes redacted JSON to stdout.

    ``--exit-code 0`` keeps a leak from being reported as a process failure;
    leaks are read from the JSON payload instead.
    """
    return [
        binary, "dir", src_path,
        "--report-format", "json",
        "--report-path", "-",
        "--redact",
        "--no-banner",
        "--exit-code", "0",
        "--log-level", "error",
    ]


def _in_excluded_dir(file_path: str, scan_path: str) -> bool:
    """Return True when ``file_path`` sits under a ``test``/``tests`` directory.

    The check is relative to ``scan_path``, so a source root that itself lives
    under a directory named ``tests`` is still scanned. A bare file named
    ``test`` is not excluded; only directory segments count.
    """
    rel = file_path
    if os.path.isabs(file_path):
        try:
            rel = os.path.relpath(file_path, scan_path)
        except ValueError:
            return False
    if rel.startswith(os.pardir):
        return False
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    return any(part in _EXCLUDED_DIR_NAMES for part in parts[:-1])


def _parse_gitleaks_output(data: Any, scan_path: str) -> list[dict[str, Any]]:
    """Convert gitleaks JSON leaks into nuguard finding dicts.

    An empty report is ``[]``; a ``null`` payload is treated the same way.
    Any other shape is rejected so that a scanner-format change surfaces as
    an error rather than silently dropping leaks.
    """
    if data is None:
        return []
    if not isinstance(data, list) or not all(isinstance(leak, dict) for leak in data):
        raise ValueError("Invalid gitleaks output shape")

    findings: list[dict[str, Any]] = []
    for leak in data:
        rule_id = str(leak.get("RuleID") or "generic-secret")
        rule_desc = str(leak.get("Description") or rule_id)
        file_path = str(leak.get("File") or scan_path)
        if _in_excluded_dir(file_path, scan_path):
            continue
        start_line = leak.get("StartLine")
        location = f"{file_path}:{start_line}" if start_line else file_path

        findings.append(
            {
                "rule_id": rule_id,
                "title": f"Hardcoded secret: {rule_id}",
                "description": (
                    f"{rule_desc.rstrip('.')}. The secret value is redacted in this report."
                ),
                "severity": _SEVERITY,
                "affected": [location],
                "remediation": (
                    "Rotate the exposed credential, remove it from the source tree, and "
                    "purge it from git history if it was committed."
                ),
                "owasp_llm_ref": "",
                "url": "",
                "source": "gitleaks",
                "scan_target": scan_path,
                "nga_rule": "",
            }
        )
    return findings
