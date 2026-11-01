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
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from nuguard.common.logging import get_logger

_log = get_logger("analysis.osv")

# Lowest version admitted by a specifier: ``==X``, ``>=X``, ``~=X``, ``^X``, ``~X``.
_SPEC_LOWER_BOUND = re.compile(r"(?:^|,)\s*(?:==|>=|~=|\^|~)\s*v?(\d[\w.\-+]*)")

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


def _advisory_aliases(detail: dict[str, Any], adv_id: str) -> list[str]:
    """Every alias OSV lists for this record (CVE, GHSA, PYSEC, ...), excluding its own id."""
    return sorted({str(a) for a in (detail.get("aliases") or []) if a and a != adv_id})


def _affected_versions(detail: dict[str, Any]) -> str:
    """Return a compact human-readable version range string."""
    ranges: list[str] = []
    for affected in (detail.get("affected") or []):
        for r in (affected.get("ranges") or []):
            events = r.get("events") or []
            introduced = next((e["introduced"] for e in events if "introduced" in e), None)
            fixed       = next((e["fixed"]      for e in events if "fixed"      in e), None)
            last        = next((e["last_affected"] for e in events if "last_affected" in e), None)
            if introduced and fixed:
                ranges.append(f">={introduced},<{fixed}")
            elif introduced and last:
                ranges.append(f">={introduced},<={last}")
            elif last:
                ranges.append(f"<={last}")
            elif introduced:
                ranges.append(f">={introduced}")
    return "; ".join(ranges) if ranges else "see advisory"


def _purl_has_version(purl: str) -> bool:
    """True when the purl pins a version, for example ``pkg:maven/group/name@1.2.3``.

    OSV answers a purl without a version with every advisory ever published for that package
    name, not the ones that affect the project, so such dependencies must not be queried.
    """
    last_segment = purl.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
    return "@" in last_segment


def _purl_version(purl: str) -> str:
    """Return the ``@version`` embedded in a PURL, or ``""``."""
    tail = purl.rsplit("/", 1)[-1].split("?", 1)[0]
    return tail.partition("@")[2] if "@" in tail else ""


def _query_version(dep: dict[str, Any]) -> str:
    """Version to ask OSV about for one dependency, or ``""`` for all versions.

    Preference: resolved ``version``, then the PURL's version, then the lowest
    version the specifier admits. An unversioned query returns advisories for
    every release, so a constraint like ``>=1.3.3`` would otherwise be flagged
    for CVEs that only affect ``<1.3.3``.
    """
    explicit = str(dep.get("version") or "").strip()
    if explicit:
        return explicit
    purl_ver = _purl_version(str(dep.get("purl") or ""))
    if purl_ver:
        return purl_ver
    match = _SPEC_LOWER_BOUND.search(str(dep.get("version_spec") or ""))
    return match.group(1) if match else ""


def _query_purl(dep: dict[str, Any]) -> str:
    """PURL to send to querybatch, with the query version appended when known."""
    purl = str(dep.get("purl") or "")
    version = _query_version(dep)
    if not version or _purl_version(purl):
        return purl
    base, sep, qualifiers = purl.partition("?")
    return f"{base}@{version}{sep}{qualifiers}"


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
    # Filter on the PURL that will actually be sent (develop appends the lowest version a
    # specifier admits). A dependency with no version even then would make OSV return every
    # advisory ever published for the package name, so it is not queried. `queries` and
    # `purls_with_meta` are built from the same survivors: OSV answers positionally.
    candidates = [(dep, _query_purl(dep)) for dep in deps if dep.get("purl")]
    unversioned = [purl for _, purl in candidates if not _purl_has_version(purl)]
    if unversioned:
        _log.info(
            "OSV: skipping %d dependenc%s without a resolved version: %s",
            len(unversioned),
            "y" if len(unversioned) == 1 else "ies",
            ", ".join(unversioned[:5]),
        )
        candidates = [(dep, purl) for dep, purl in candidates if _purl_has_version(purl)]
    if not candidates:
        return []

    purls_with_meta = [
        (dep.get("purl", ""), dep.get("name", ""), dep.get("version_spec", ""))
        for dep, _ in candidates
    ]
    queries = [{"package": {"purl": purl}} for _, purl in candidates]

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
            "aliases":         _advisory_aliases(detail, adv_id),
            "summary":         summary,
            "severity":        sev,
            "affected_versions": _affected_versions(detail),
            "url":             f"https://osv.dev/vulnerability/{adv_id}",
        })

    # Sort by severity then advisory ID
    _sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
    findings.sort(key=lambda f: (_sev_order.get(f["severity"], 9), f["advisory_id"]))
    return findings
