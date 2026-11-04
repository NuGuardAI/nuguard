"""pip-audit dependency vulnerability scanner plugin for nuguard.

Wraps the ``pip-audit`` CLI to audit the *resolved* Python dependency tree of
every manifest recorded in the SBOM.  Unlike the offline OSV lookup, pip-audit
resolves transitive dependencies, so it reports advisories for packages that
an SBOM floor (``chromadb>=0.5.0``) never names directly.

Manifest selection
------------------
- ``pyproject.toml`` deps: the containing project directory is audited.
- ``requirements*.txt`` deps: the requirements file is audited with ``-r``.

Each manifest is resolved against ``config["source_path"]`` and the SBOM
``target`` directory, mirroring the Checkov plugin.

pip-audit reports no severity.  Findings are emitted as ``MEDIUM`` and the
description says so; the analyzer prefers another scanner's severity for the
same advisory when one exists.

The plugin is skipped when ``pip-audit`` is not on PATH or when the SBOM has
no PyPI manifests.

Usage
-----
::

    from nuguard.analysis.plugins.pip_audit_scanner import PipAuditScannerPlugin
    result = PipAuditScannerPlugin().run(sbom_dict, config={})
"""

from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin
from nuguard.analysis.plugins.nga_rules import _finding_title
from nuguard.analysis.plugins.scanner_runtime import run_scanner
from nuguard.common.logging import get_logger

_log = get_logger("analysis.plugins.pip_audit")

_ASSUMED_SEVERITY = "MEDIUM"
_ASSUMED_NOTE = "Severity not reported by pip-audit (defaulted to MEDIUM)."
_OSV_URL = "https://osv.dev/vulnerability/{}"
_VERSION_PART = re.compile(r"\d+")


def _pip_audit_path() -> str | None:
    return shutil.which("pip-audit")


class PipAuditScannerPlugin(AnalysisPlugin):
    """Run pip-audit against the Python manifests recorded in the SBOM."""

    name = "pip-audit"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        """Audit each PyPI manifest referenced by ``sbom["deps"]``."""
        binary = _pip_audit_path()
        if binary is None:
            _log.info(
                "pip-audit not found on PATH; install with `pip install pip-audit` "
                "to enable resolved-dependency auditing"
            )
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="pip-audit not installed — dependency audit skipped",
            )

        targets = _collect_manifest_targets(sbom, config)
        if not targets:
            _log.info("pip-audit: no Python manifests found in SBOM deps")
            return AnalysisResult(
                status="skipped",
                plugin=self.name,
                message="no Python manifests found in SBOM — pip-audit skipped",
            )

        result = run_scanner(
            self.name,
            targets,
            lambda path: _pip_audit_command(binary, path),
            _parse_pip_audit_output,
            timeout=config.get("pip_audit_timeout", 300.0),
            total_timeout=config.get("pip_audit_total_timeout", 600.0),
        )
        result.findings = _dedupe_findings(result.findings)
        result.details["total"] = len(result.findings)
        result.details["manifests_scanned"] = sorted(targets)
        return result


def _collect_manifest_targets(sbom: dict[str, Any], config: dict[str, Any]) -> set[str]:
    """Return audit targets: project directories (pyproject) or requirements files.

    Resolution order for each ``source_file`` matches the Checkov plugin:
    as-is, then relative to ``config["source_path"]``, then relative to the
    SBOM ``target`` directory.  Unresolvable manifests are skipped.
    """
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

    def _resolve(raw: str) -> Path | None:
        p = Path(raw)
        if p.is_file():
            return p
        for root in roots:
            resolved = root / p
            if resolved.is_file():
                return resolved
        return None

    targets: set[str] = set()
    for dep in sbom.get("deps") or []:
        if not isinstance(dep, dict):
            continue
        source_file = str(dep.get("source_file") or "")
        purl = str(dep.get("purl") or "")
        if not source_file or not (purl.startswith("pkg:pypi/") or _is_python_manifest(source_file)):
            continue
        resolved = _resolve(source_file)
        if resolved is None:
            continue
        if resolved.name == "pyproject.toml":
            if _has_project_table(resolved):
                targets.add(str(resolved.parent))
        elif _is_requirements_file(resolved.name):
            targets.add(str(resolved))
    return targets


def _has_project_table(pyproject: Path) -> bool:
    """Return True when pip-audit can read the file (it requires a ``[project]`` table).

    pyproject files without one (e.g. tool-only or Poetry-style manifests) make
    pip-audit exit 1 with a stderr error and no JSON, so they are skipped here.
    """
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return isinstance(data.get("project"), dict)


def _is_python_manifest(source_file: str) -> bool:
    name = Path(source_file).name
    return name == "pyproject.toml" or _is_requirements_file(name)


def _is_requirements_file(name: str) -> bool:
    return name.startswith("requirements") and name.endswith(".txt")


def _pip_audit_command(binary: str, path: str) -> list[str]:
    """Audit a project directory, or a requirements file via ``-r``."""
    base = [binary]
    if not Path(path).is_dir():
        base += ["-r", path]
    else:
        base.append(path)
    return base + [
        "--vulnerability-service", "osv",
        "-f", "json",
        "--progress-spinner", "off",
    ]


def _fix_version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in _VERSION_PART.findall(version))


def _best_fix_version(fix_versions: list[str]) -> str | None:
    """Return the highest fix version so one upgrade clears every listed advisory."""
    candidates = [v for v in fix_versions if _VERSION_PART.search(v)]
    if not candidates:
        return None
    return max(candidates, key=_fix_version_key)


def _parse_pip_audit_output(data: Any, scan_path: str) -> list[dict[str, Any]]:
    """Convert pip-audit JSON into nuguard raw finding dicts (one per dep/advisory)."""
    if not isinstance(data, dict):
        return []
    findings: list[dict[str, Any]] = []
    for dep in data.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        name = str(dep.get("name") or "")
        version = str(dep.get("version") or "")
        for vuln in dep.get("vulns") or []:
            if not isinstance(vuln, dict) or not vuln.get("id"):
                continue
            findings.append(_vuln_to_finding(name, version, vuln))
    return findings


def _vuln_to_finding(name: str, version: str, vuln: dict[str, Any]) -> dict[str, Any]:
    adv_id = str(vuln["id"])
    aliases = sorted({adv_id, *(str(a) for a in vuln.get("aliases") or [] if a)}, key=_alias_sort_key)
    description = str(vuln.get("description") or "")
    title = _finding_title(name, adv_id, description)
    title = f"{title} [{', '.join(aliases)}]"
    fixed = _best_fix_version([str(v) for v in vuln.get("fix_versions") or []])
    fix_note = f" (fix available: {fixed})" if fixed else ""
    return {
        "rule_id": adv_id,
        "severity": _ASSUMED_SEVERITY,
        "title": title,
        "description": (
            f"{description or adv_id}  Package: {name} {version}.  {_ASSUMED_NOTE}"
        ),
        "affected": [f"pkg:pypi/{name}@{version}"],
        "remediation": (
            f"Upgrade {name} to a version outside the affected range{fix_note}. "
            f"See {_OSV_URL.format(adv_id)} for details."
        ),
        "source": "pip-audit",
        "advisory_url": _OSV_URL.format(adv_id),
        "cve_ids": [a for a in aliases if a.startswith("CVE-")],
        "fixed_version": fixed,
    }


def _alias_sort_key(alias: str) -> tuple[int, str]:
    order = {"CVE": 0, "GHSA": 1, "PYSEC": 2}
    prefix = alias.split("-", 1)[0]
    return (order.get(prefix, 3), alias)


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated (package, version, advisory) rows from multiple paths."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for finding in findings:
        affected = (finding.get("affected") or [""])[0]
        key = (affected, str(finding.get("rule_id")), str(finding.get("source")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique
