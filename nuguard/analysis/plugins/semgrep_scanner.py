"""Semgrep static code analysis plugin for nuguard.

Runs three bundled rulesets — ``ai-security.yaml`` (AI/LLM-specific
anti-patterns, Python/Go), ``java-ai-security.yaml`` (Java AI security),
and ``generic-security.yaml`` (classic
Injection/XSS/path-traversal/insecure-deserialization/weak-crypto/
hardcoded-secret patterns, JavaScript/TypeScript) — plus any additional
rules in ``config["semgrep_rules"]``, against source paths extracted from
the SBOM. The bundled rulesets are deliberately non-overlapping in language
coverage: ``ai-security.yaml`` targets Python/Go AI code,
``java-ai-security.yaml`` targets Java AI code, and
``generic-security.yaml`` targets the non-AI JS/TS backends
NuGuard's AI-security rules don't otherwise touch.

The plugin is skipped (returns status ``"skipped"``) when:
- ``semgrep`` is not installed / not on PATH
- No source paths are found in the SBOM

Execution and output errors are reported as incomplete scans.

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

import shutil
from pathlib import Path
from typing import Any

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin
from nuguard.analysis.plugins.scanner_runtime import run_scanner
from nuguard.common.logging import get_logger

_log = get_logger("analysis.plugins.semgrep")

# Bundled rulesets: AI/LLM patterns for Python/Go and Java, plus classic
# non-AI vulnerability classes for JavaScript/TypeScript.
_BUNDLED_RULES: list[Path] = [
    Path(__file__).parent / "semgrep_rules" / "ai-security.yaml",
    Path(__file__).parent / "semgrep_rules" / "java-ai-security.yaml",
    Path(__file__).parent / "semgrep_rules" / "generic-security.yaml",
]

# Semgrep severity → nuguard severity label
_SEV_MAP: dict[str, str] = {
    "ERROR": "HIGH",
    "WARNING": "MEDIUM",
    "INFO": "INFO",
}


def _semgrep_path() -> str | None:
    return shutil.which("semgrep")


class SemgrepScannerPlugin(AnalysisPlugin):
    """Run Semgrep static analysis with the bundled AI-security + generic-security rulesets."""

    name = "semgrep"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        """Scan source paths referenced in the SBOM with Semgrep.

        Source paths are extracted from node ``metadata.source_path`` or
        ``metadata.extras.source_path``.  Both bundled rulesets
        (``ai-security.yaml``, ``generic-security.yaml``) are always
        included; additional rules can be specified via
        ``config["semgrep_rules"]``.
        """
        binary = _semgrep_path()
        if binary is None:
            _log.info(
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
            _log.info("semgrep: no source paths found in SBOM nodes")
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="no source paths found in SBOM — semgrep scan skipped",
            )

        rule_files: list[str] = [str(p) for p in _BUNDLED_RULES]
        extra_rules = config.get("semgrep_rules")
        if extra_rules:
            if not Path(str(extra_rules)).exists():
                _log.warning("semgrep: additional rules path missing; scan incomplete")
                return AnalysisResult(status="error", plugin=self.name, message="Additional Semgrep rules path does not exist")
            rule_files.append(str(extra_rules))

        result = run_scanner(
            self.name, src_paths,
            lambda path: _semgrep_command(binary, path, rule_files, config.get("semgrep_exclude_tests", True)),
            _parse_semgrep_output,
            timeout=config.get("semgrep_timeout", 120.0),
            total_timeout=config.get("semgrep_total_timeout", 300.0),
        )
        result.details["paths_scanned"] = result.details.get("scanned_paths", [])
        result.details["rule_files"] = rule_files
        return result



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


def _semgrep_command(binary: str, src_path: str, rule_files: list[str], exclude_tests: bool) -> list[str]:
    """Build a local Semgrep scan, avoiding optional version-check network calls."""
    cmd = [binary, "scan", "--json", "--quiet", "--disable-version-check", "--metrics", "off"]
    for rf in rule_files:
        cmd += ["--config", rf]
    if exclude_tests:
        cmd += ["--exclude", "tests/", "--exclude", "test_*.py", "--exclude", "*_test.py"]
    return [*cmd, src_path]


def _run_semgrep(binary: str, src_path: str, rule_files: list[str], timeout: float, exclude_tests: bool) -> list[dict[str, Any]]:
    """Compatibility helper for one path; plugin.run exposes scan diagnostics."""
    return run_scanner(
        "semgrep", [src_path], lambda path: _semgrep_command(binary, path, rule_files, exclude_tests),
        _parse_semgrep_output, timeout=timeout,
    ).findings


def _parse_semgrep_output(data: dict[str, Any], scan_path: str) -> list[dict[str, Any]]:
    """Convert semgrep JSON results into nuguard finding dicts."""
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("Invalid Semgrep output shape")
    findings: list[dict[str, Any]] = []
    for r in data.get("results") or []:
        check_id = r.get("check_id", "")
        msg = r.get("extra", {}).get("message", check_id)
        severity = _SEV_MAP.get(
            str(r.get("extra", {}).get("severity") or r.get("severity", "WARNING")).upper(),
            "MEDIUM",
        )
        file_path = r.get("path", scan_path)
        start_line = r.get("start", {}).get("line")
        location = f"{file_path}:{start_line}" if start_line else file_path
        meta = r.get("extra", {}).get("metadata") or {}
        owasp = meta.get("owasp", "")
        nga_rule = meta.get("nuguard_rule", "")
        # Only the OWASP LLM Top 10 short-label ("LLM0x: ...") maps onto owasp_llm_ref;
        # generic web-appsec labels ("A02: ...") aren't LLM Top 10 citations.
        owasp_llm_ref = owasp if owasp.upper().startswith("LLM") else ""

        findings.append(
            {
                "rule_id": check_id,
                "title": check_id.split(".")[-1].replace("-", " ").title(),
                "description": msg,
                "severity": severity,
                "affected": [location],
                "remediation": "",
                "owasp_llm_ref": owasp_llm_ref,
                "url": "",
                "source": "semgrep",
                "scan_target": scan_path,
                "nga_rule": nga_rule,
            }
        )
    return findings
