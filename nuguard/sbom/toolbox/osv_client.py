"""OSV (Open Source Vulnerabilities) API client.

Queries https://api.osv.dev/v1/querybatch for known CVEs affecting a list of
``PackageDep``-like dicts (or serialised ``PackageDep`` objects).

Each item in the input list must have at minimum:
  - ``name``        — package name
  - ``purl``        — Package URL (pkg:pypi/..., pkg:npm/..., etc.)
  - ``version_spec`` — raw version specifier (``==X.Y.Z`` for pinned)

Returns a flat list of finding dicts, one per (dep, advisory) pair:
  - ``advisory_id``       — OSV advisory ID (e.g. GHSA-… or PYSEC-…)
  - ``dep_name``          — package name
  - ``purl``              — PURL of the affected dep
  - ``severity``          — CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN
  - ``summary``           — short advisory description
  - ``url``               — advisory URL on osv.dev
  - ``affected_versions`` — human-readable affected range string
  - ``cve_ids``           — list of CVE aliases (may be empty)
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)

_OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"

# Map PURL ecosystem prefix → OSV ecosystem name
_PURL_TO_ECOSYSTEM: dict[str, str] = {
    "pypi":  "PyPI",
    "npm":   "npm",
    "maven": "Maven",
    "cargo": "crates.io",
    "nuget": "NuGet",
    "gem":   "RubyGems",
    "go":    "Go",
}

# CVSS score thresholds for normalised severity labels
_SCORE_TO_SEV: list[tuple[float, str]] = [
    (9.0, "CRITICAL"),
    (7.0, "HIGH"),
    (4.0, "MEDIUM"),
    (0.1, "LOW"),
]


def _purl_parts(purl: str) -> tuple[str, str, str | None]:
    """Return (ecosystem, name, version) from a PURL string.

    Returns ("", purl, None) if the PURL cannot be parsed.
    """
    m = re.match(r"pkg:([^/]+)/([^@]+)(?:@(.+))?", purl)
    if not m:
        return "", purl, None
    eco_key = m.group(1).lower().split("+")[0]
    ecosystem = _PURL_TO_ECOSYSTEM.get(eco_key, eco_key.capitalize())
    name = m.group(2).replace("%40", "@")
    # For scoped npm packages, keep the @ prefix
    if eco_key == "npm" and purl.split("/", 1)[1].startswith("%40"):
        name = "@" + name.lstrip("@")
    version = m.group(3)
    return ecosystem, name, version


def _infer_severity(vuln: dict[str, Any]) -> str:
    """Derive a CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN label from an OSV vuln record."""
    # Try CVSS score from severity list
    for sev_entry in vuln.get("severity") or []:
        score_str = sev_entry.get("score", "")
        # CVSS v3 score looks like "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
        m = re.search(r"/(\d+\.\d+)$", score_str)
        if not m:
            # Try plain float
            try:
                score = float(score_str)
            except (ValueError, TypeError):
                continue
        else:
            score = float(m.group(1))
        for threshold, label in _SCORE_TO_SEV:
            if score >= threshold:
                return label

    # Fall back to database_specific.severity
    db = vuln.get("database_specific") or {}
    sev = (db.get("severity") or "").upper()
    if sev in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return sev

    return "UNKNOWN"


def _affected_range_str(vuln: dict[str, Any], name: str) -> str:
    """Return a human-readable affected version range string."""
    ranges: list[str] = []
    for affected in vuln.get("affected") or []:
        pkg = (affected.get("package") or {})
        if pkg.get("name", "").lower() not in (name.lower(), ""):
            continue
        for r in affected.get("ranges") or []:
            parts: list[str] = []
            for event in r.get("events") or []:
                if "introduced" in event:
                    v = event["introduced"]
                    parts.append(f">= {v}" if v != "0" else "all versions")
                elif "fixed" in event:
                    parts.append(f"< {event['fixed']}")
                elif "last_affected" in event:
                    parts.append(f"<= {event['last_affected']}")
            if parts:
                ranges.append(", ".join(parts))
    return "; ".join(ranges) if ranges else "see advisory"


def query_osv(
    deps: list[dict[str, Any]],
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Query OSV batch API and return a flat list of vulnerability findings.

    Parameters
    ----------
    deps:
        Sequence of dep dicts (serialised ``PackageDep`` or plain dicts with
        ``name``, ``purl``, and optionally ``version_spec``).
    timeout:
        Network timeout in seconds.

    Returns
    -------
    list of finding dicts — see module docstring for keys.
    """
    if not deps:
        return []

    queries: list[dict[str, Any]] = []
    meta: list[tuple[str, str, str]] = []  # (dep_name, purl, version_or_empty)

    for dep in deps:
        name = dep.get("name") or ""
        purl = dep.get("purl") or ""
        if not name and not purl:
            continue

        ecosystem, pkg_name, purl_version = _purl_parts(purl)
        version_spec: str = dep.get("version_spec") or ""

        # Prefer explicit pinned version (==X.Y.Z) over PURL-embedded version
        pin_match = re.match(r"==\s*([\w.\-+]+)", version_spec)
        version = pin_match.group(1) if pin_match else (purl_version or "")

        if not ecosystem or not pkg_name:
            _log.debug("skipping dep with unparseable PURL: %s", purl)
            continue

        query: dict[str, Any] = {"package": {"name": pkg_name, "ecosystem": ecosystem}}
        if version:
            query["version"] = version
        else:
            # Without a version OSV searches all versions — useful but can be noisy
            _log.debug("no pinned version for %s, querying all versions", pkg_name)

        queries.append(query)
        meta.append((name or pkg_name, purl, version))

    if not queries:
        return []

    payload = json.dumps({"queries": queries}).encode()
    req = urllib.request.Request(
        _OSV_BATCH_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        _log.warning("OSV API HTTP error %s: %s", exc.code, exc.reason)
        return []
    except urllib.error.URLError as exc:
        _log.warning("OSV API network error: %s", exc.reason)
        return []
    except Exception as exc:  # noqa: BLE001
        _log.warning("OSV API unexpected error: %s", exc)
        return []

    results_raw = body.get("results") or []
    findings: list[dict[str, Any]] = []

    for idx, batch_result in enumerate(results_raw):
        if idx >= len(meta):
            break
        dep_name, dep_purl, _ = meta[idx]
        for vuln in batch_result.get("vulns") or []:
            advisory_id = vuln.get("id", "OSV-UNKNOWN")
            cve_ids = [a for a in vuln.get("aliases") or [] if a.startswith("CVE-")]
            severity = _infer_severity(vuln)
            summary = vuln.get("summary") or ""
            affected_str = _affected_range_str(vuln, dep_name)
            url = f"https://osv.dev/vulnerability/{advisory_id}"

            findings.append(
                {
                    "advisory_id": advisory_id,
                    "dep_name": dep_name,
                    "purl": dep_purl,
                    "severity": severity,
                    "summary": summary,
                    "url": url,
                    "affected_versions": affected_str,
                    "cve_ids": cve_ids,
                }
            )

    _log.info(
        "OSV query: %d dep(s) → %d finding(s)", len(queries), len(findings)
    )
    return findings
