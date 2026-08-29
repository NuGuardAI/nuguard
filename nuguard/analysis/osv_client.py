"""OSV (Open Source Vulnerabilities) API client.

Queries https://api.osv.dev for known vulnerabilities in package dependencies
listed in a NuGuard SBOM ``deps`` array.

Flow
----
1. ``querybatch`` — one POST with all PURLs; returns advisory IDs only.
2. Fetch individual vuln details for advisories found (capped to avoid runaway
   calls on large result sets).
3. Parse severity from ``database_specific.severity`` or CVSS vector.

All network errors are caught; callers receive an empty list on failure so the
rest of the vulnerability scan still runs.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from nuguard.common.logging import get_logger

_log = get_logger("analysis.osv")

_BATCH_URL  = "https://api.osv.dev/v1/querybatch"
_VULN_URL   = "https://api.osv.dev/v1/vulns/{id}"
_TIMEOUT    = 15.0
_MAX_DETAIL = 30  # max individual vuln fetches per scan
# Max concurrent detail-fetch GETs — independent network calls, so a small
# thread pool turns the sequential fetch loop into a fan-out.
_DETAIL_FETCH_CONCURRENCY = 8

# Map OSV database_specific.severity → our labels
_DB_SEV_MAP: dict[str, str] = {
    "critical":  "CRITICAL",
    "high":      "HIGH",
    "moderate":  "MEDIUM",
    "medium":    "MEDIUM",
    "low":       "LOW",
    "none":      "INFO",
}

# Approximate CVSS v3 base score ranges → our labels
_CVSS_RANGES = [
    (9.0, "CRITICAL"),
    (7.0, "HIGH"),
    (4.0, "MEDIUM"),
    (0.1, "LOW"),
]


def _get_json(url: str, timeout: float = _TIMEOUT) -> dict[str, Any]:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]


def _post_json(url: str, body: dict[str, Any], timeout: float = _TIMEOUT) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]


def _severity_from_detail(detail: dict[str, Any]) -> str:
    """Extract severity label from a full OSV vulnerability record."""
    # Prefer the human-readable label in database_specific
    db_sev = (detail.get("database_specific") or {}).get("severity", "")
    if db_sev:
        mapped = _DB_SEV_MAP.get(db_sev.lower())
        if mapped:
            return mapped

    # Fall back to CVSS vector if present
    for sev_entry in detail.get("severity") or []:
        score_str: str = sev_entry.get("score", "")
        # score_str is a CVSS vector, e.g. "CVSS:3.1/AV:N/AC:L/..."
        # Crude base-score approximation from impact metrics (C/I/A)
        # Impact values: N=0, L=1, H=2
        if "CVSS:3" in score_str.upper():
            parts = dict(kv.split(":") for kv in score_str.split("/")[1:] if ":" in kv)
            weights = {"H": 2, "L": 1, "N": 0}
            impact = sum(weights.get(parts.get(k, "N"), 0) for k in ("C", "I", "A"))
            # Max impact = 6 → CRITICAL; ≥4 HIGH; ≥2 MEDIUM; else LOW
            if impact >= 5:
                return "CRITICAL"
            elif impact >= 4:
                return "HIGH"
            elif impact >= 2:
                return "MEDIUM"
            return "LOW"

    return "UNKNOWN"


def _cve_aliases(detail: dict[str, Any]) -> list[str]:
    return [a for a in (detail.get("aliases") or []) if a.startswith("CVE-")]


def _affected_versions(detail: dict[str, Any]) -> str:
    """Return a compact human-readable version range string."""
    ranges: list[str] = []
    for affected in (detail.get("affected") or []):
        for r in (affected.get("ranges") or []):
            events = r.get("events") or []
            introduced = next((e["introduced"] for e in events if "introduced" in e), None)
            fixed       = next((e["fixed"]      for e in events if "fixed"      in e), None)
            if introduced and fixed:
                ranges.append(f">={introduced},<{fixed}")
            elif introduced:
                ranges.append(f">={introduced}")
    return "; ".join(ranges) if ranges else "see advisory"


def query_osv(
    deps: list[dict[str, Any]],
    timeout: float = _TIMEOUT,
) -> list[dict[str, Any]]:
    """Return a list of OSV finding dicts for each vulnerable dependency.

    Each finding dict has keys:
      ``dep_name``, ``dep_version``, ``purl``,
      ``advisory_id``, ``cve_ids``, ``summary``,
      ``severity``, ``affected_versions``, ``url``
    """
    purls_with_meta = [
        (dep.get("purl", ""), dep.get("name", ""), dep.get("version_spec", ""))
        for dep in deps
        if dep.get("purl")
    ]
    if not purls_with_meta:
        return []

    queries = [{"package": {"purl": purl}} for purl, _, _ in purls_with_meta]

    try:
        batch_resp = _post_json(_BATCH_URL, {"queries": queries}, timeout=timeout)
    except Exception as exc:
        _log.warning("OSV querybatch failed: %s", exc)
        return []

    # Collect (purl_meta, advisory_id) pairs
    found: list[tuple[tuple[str, str, str], str]] = []
    for meta, result in zip(purls_with_meta, batch_resp.get("results") or []):
        for vuln_stub in result.get("vulns") or []:
            adv_id = vuln_stub.get("id")
            if adv_id:
                found.append((meta, adv_id))

    if not found:
        return []

    # Collect the (deduplicated, capped) set of advisory ids to fetch details for
    seen_ids: set[str] = set()
    to_fetch: list[str] = []
    for _, adv_id in found:
        if adv_id in seen_ids or len(to_fetch) >= _MAX_DETAIL:
            continue
        seen_ids.add(adv_id)
        to_fetch.append(adv_id)

    # Each detail fetch is an independent GET — run them concurrently instead
    # of one at a time.
    detail_map: dict[str, dict[str, Any]] = {}
    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(_DETAIL_FETCH_CONCURRENCY, len(to_fetch))) as pool:
            future_to_id = {
                pool.submit(_get_json, _VULN_URL.format(id=adv_id), timeout): adv_id
                for adv_id in to_fetch
            }
            for future in as_completed(future_to_id):
                adv_id = future_to_id[future]
                try:
                    detail_map[adv_id] = future.result()
                except Exception as exc:
                    _log.warning("OSV detail fetch %s failed: %s", adv_id, exc)
                    detail_map[adv_id] = {"id": adv_id}

    findings: list[dict[str, Any]] = []
    for (purl, dep_name, dep_version), adv_id in found:
        detail  = detail_map.get(adv_id, {"id": adv_id})
        sev     = _severity_from_detail(detail)
        cve_ids = _cve_aliases(detail)
        summary = (detail.get("summary") or "").strip() or adv_id

        findings.append({
            "dep_name":        dep_name,
            "dep_version":     dep_version,
            "purl":            purl,
            "advisory_id":     adv_id,
            "cve_ids":         cve_ids,
            "summary":         summary,
            "severity":        sev,
            "affected_versions": _affected_versions(detail),
            "url":             f"https://osv.dev/vulnerability/{adv_id}",
        })

    # Sort by severity then advisory ID
    _sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
    findings.sort(key=lambda f: (_sev_order.get(f["severity"], 9), f["advisory_id"]))
    return findings
