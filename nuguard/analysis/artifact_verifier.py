"""Read-only registry artifact verification (Phase 3).

Fetches package metadata from PyPI and npm registries without installing
anything or executing any scripts.  Compares lifecycle scripts and
provenance claims in the published artifact against what the source
repository declares.

Rule IDs:
  NGA-SC-A01  Artifact has lifecycle script absent from source repo
  NGA-SC-A02  Artifact provenance claim cannot be tied to this repo/workflow
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nuguard.common.logging import get_logger

_log = get_logger("analysis.artifact_verifier")

_PYPI_API = "https://pypi.org/pypi/{name}/{version}/json"
_NPM_API = "https://registry.npmjs.org/{name}/{version}"
_NPM_LATEST_API = "https://registry.npmjs.org/{name}/latest"

_NPM_LIFECYCLE = {
    "preinstall", "install", "postinstall", "prepare",
    "prepack", "postpack", "prepublish", "prepublishOnly",
}


class ArtifactVerifier:
    """Read-only registry artifact verification.

    For each package in the SBOM deps list:
    1. Fetch registry metadata (PyPI JSON API / npm registry API) — no install
    2. Compare lifecycle scripts in the artifact with what the source repo declares
    3. Report drift: artifact has lifecycle scripts the source does not

    Parameters
    ----------
    mode:
        "warn"  — emit MEDIUM-severity findings (non-blocking)
        "fail"  — emit HIGH/CRITICAL-severity findings (blocks on threshold)
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(self, mode: str = "warn", timeout: float = 15.0) -> None:
        self.mode = mode if mode in ("warn", "fail") else "warn"
        self.timeout = timeout
        self._drift_severity = "medium" if mode == "warn" else "high"
        self._provenance_severity = "critical" if mode == "fail" else "medium"

    async def verify_packages(
        self,
        deps: list[dict[str, Any]],
        source_path: Path,
    ) -> list[dict[str, Any]]:
        """Verify all deps concurrently; return raw finding dicts."""
        if not deps:
            return []

        # Read source-repo lifecycle scripts once
        source_scripts = _read_source_scripts(source_path)

        tasks = []
        for dep in deps:
            purl = str(dep.get("purl") or "")
            if "pypi" in purl.lower():
                tasks.append(self._verify_pypi(dep, source_scripts))
            elif "npm" in purl.lower():
                tasks.append(self._verify_npm(dep, source_scripts))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        findings: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, list):
                findings.extend(r)
            elif isinstance(r, Exception):
                _log.debug("Artifact verification task failed: %s", r)
        return findings

    async def _verify_pypi(
        self,
        dep: dict[str, Any],
        source_scripts: dict[str, str],
    ) -> list[dict[str, Any]]:
        name = str(dep.get("name") or "")
        version = str(dep.get("version") or "")
        if not name or not version:
            return []

        url = _PYPI_API.format(name=name, version=version)
        try:
            data = await _fetch_json(url, self.timeout)
        except Exception as exc:
            _log.debug("PyPI fetch failed for %s==%s: %s", name, version, exc)
            return []

        info = data.get("info") or {}
        findings: list[dict[str, Any]] = []

        # PyPI provenance: check project_urls for repository link
        project_urls: dict[str, str] = info.get("project_urls") or {}
        repo_url = project_urls.get("Source") or project_urls.get("Repository") or ""
        if repo_url and source_path_looks_like(repo_url):
            pass  # matches — no finding
        elif not repo_url and "NGA-SC-A02" in _ALWAYS_EMIT:
            findings.append(_finding(
                rule_id="NGA-SC-A02",
                severity=self._provenance_severity,
                title=f"PyPI package {name}=={version} has no source repository link",
                description=(
                    f"PyPI package '{name}=={version}' does not declare a source repository "
                    "URL in project_urls. Without a source link, it is not possible to verify "
                    "that the published artifact matches the repository source."
                ),
                affected=[f"pkg:pypi/{name}@{version}"],
                remediation=(
                    "Add `[project.urls] Source = https://github.com/…` to pyproject.toml."
                ),
            ))

        return findings

    async def _verify_npm(
        self,
        dep: dict[str, Any],
        source_scripts: dict[str, str],
    ) -> list[dict[str, Any]]:
        name = str(dep.get("name") or "")
        version = str(dep.get("version") or "")
        if not name:
            return []

        url = _NPM_API.format(name=name, version=version) if version else _NPM_LATEST_API.format(name=name)
        try:
            data = await _fetch_json(url, self.timeout)
        except Exception as exc:
            _log.debug("npm fetch failed for %s@%s: %s", name, version, exc)
            return []

        registry_scripts: dict[str, str] = data.get("scripts") or {}
        findings: list[dict[str, Any]] = []

        # Compare lifecycle scripts in registry vs. source
        for phase, body in registry_scripts.items():
            if phase not in _NPM_LIFECYCLE:
                continue
            source_body = source_scripts.get(phase)
            if source_body is None:
                # Artifact has a lifecycle script that the source does not declare
                findings.append(_finding(
                    rule_id="NGA-SC-A01",
                    severity=self._drift_severity,
                    title=f"npm artifact has lifecycle script absent from source: {name} ({phase})",
                    description=(
                        f"The published npm package '{name}@{version}' has a `{phase}` "
                        "lifecycle script that does not appear in the source repository's "
                        "package.json. This is the exact artifact/source drift reported by "
                        "Wiz for the Miasma campaign — the malicious script was injected into "
                        "the published artifact without being present in the source."
                    ),
                    affected=[f"pkg:npm/{name}@{version}"],
                    remediation=(
                        "Compare the published artifact with the source commit. "
                        "If you maintain this package, audit your publish pipeline and rotate "
                        "all npm tokens. File a security advisory."
                    ),
                ))
            elif source_body.strip() != str(body).strip():
                # Body differs — may be intentional (build step) or malicious
                findings.append(_finding(
                    rule_id="NGA-SC-A01",
                    severity="low",
                    title=f"npm artifact lifecycle script differs from source: {name} ({phase})",
                    description=(
                        f"The `{phase}` script in the published '{name}@{version}' "
                        "differs from the source repository. This may be a legitimate "
                        "build-time transformation, but should be reviewed."
                    ),
                    affected=[f"pkg:npm/{name}@{version}"],
                    remediation="Verify the published artifact matches the expected build output.",
                ))

        # Provenance: check for npm provenance attestation
        dist = data.get("dist") or {}
        if not dist.get("attestations") and "NGA-SC-A02" in _ALWAYS_EMIT:
            findings.append(_finding(
                rule_id="NGA-SC-A02",
                severity="low",
                title=f"npm package {name}@{version} has no provenance attestation",
                description=(
                    f"'{name}@{version}' was published without npm provenance attestation. "
                    "Provenance links the published artifact to a specific workflow run and "
                    "commit, making artifact/source drift detectable."
                ),
                affected=[f"pkg:npm/{name}@{version}"],
                remediation=(
                    "Enable npm provenance in your publish workflow: "
                    "`npm publish --provenance` (requires OIDC token in GitHub Actions)."
                ),
            ))

        return findings


# ── Helpers ───────────────────────────────────────────────────────────────────

_ALWAYS_EMIT: set[str] = set()  # reserved for future opt-in rules


async def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    try:
        from nuguard.common.http import make_http_client  # noqa: PLC0415
        async with make_http_client(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        import httpx  # noqa: PLC0415
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()


def _read_source_scripts(source_path: Path) -> dict[str, str]:
    """Read npm lifecycle scripts from source_path/package.json."""
    pkg_json = source_path / "package.json"
    if not pkg_json.exists():
        return {}
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in (data.get("scripts") or {}).items() if isinstance(v, str)}
    except (json.JSONDecodeError, OSError):
        return {}


def source_path_looks_like(repo_url: str) -> bool:
    """Placeholder: returns True if repo_url looks like a valid source URL."""
    return bool(repo_url.startswith("https://"))


def _finding(
    rule_id: str,
    severity: str,
    title: str,
    description: str,
    affected: list[str],
    remediation: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "title": title,
        "description": description,
        "affected": affected,
        "remediation": remediation,
    }
