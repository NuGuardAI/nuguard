"""Grype integration for nuguard vulnerability scanning.

Wraps the ``grype`` CLI binary as a subprocess.  Grype queries multiple
vulnerability databases (NVD, GitHub Advisory, OSV, distro advisories) and
can scan:

  - CycloneDX / SPDX SBOMs          → ``grype sbom:<file.json>``
  - Container images by reference    → ``grype <registry>/<image>:<tag>``

Usage
-----
::

    findings = query_grype_sbom(sbom_dict)              # scan package deps via CycloneDX
    findings += query_grype_images(container_nodes)     # scan CONTAINER_IMAGE nodes

Both functions return an empty list (with a log warning) when:
  - grype is not installed / not on PATH
  - grype exits with a non-zero code
  - any network or parse error occurs

The returned list elements share the same shape as OSV findings:
  {dep_name, dep_version, purl, advisory_id, cve_ids, summary, severity,
   affected_versions, url, source="grype"}
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from nuguard.common.logging import get_logger

_log = get_logger("analysis.grype")

# Max concurrent `grype <image>` subprocesses when scanning multiple container
# images — each is an independent, blocking subprocess call, so a small
# thread pool turns an O(n) sequential scan into ~O(n / concurrency).
_IMAGE_SCAN_CONCURRENCY = 4

# Grype severity labels → normalised values (Grype uses title-case)
_SEV_MAP: dict[str, str] = {
    "critical":    "CRITICAL",
    "high":        "HIGH",
    "medium":      "MEDIUM",
    "low":         "LOW",
    "negligible":  "LOW",
    "unknown":     "UNKNOWN",
}


def _grype_path() -> str | None:
    """Return the path to the grype binary, or None if not installed."""
    return shutil.which("grype")


def _run_grype(
    target: str,
    timeout: float = 180.0,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """Run ``grype <target> --output json --quiet`` and return parsed matches.

    Retries up to *max_retries* times on timeout (e.g. first run while the
    vulnerability DB is being downloaded).  Returns an empty list on all errors.
    """
    binary = _grype_path()
    if binary is None:
        _log.warning(
            "grype binary not found on PATH; install from https://github.com/anchore/grype "
            "to enable Grype scanning"
        )
        return []

    cmd = [binary, target, "--output", "json", "--quiet"]
    _log.debug("running: %s", " ".join(cmd))
    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if attempt < max_retries:
                _log.warning(
                    "grype timed out scanning %s (timeout=%.0fs, attempt %d/%d) — retrying",
                    target, timeout, attempt, max_retries,
                )
                continue
            _log.warning(
                "grype timed out scanning %s (timeout=%.0fs) after %d attempts — giving up",
                target, timeout, max_retries,
            )
            return []
        except OSError as exc:
            _log.warning("grype process error for %s: %s", target, exc)
            return []

        if result.returncode not in (0, 1):
            # Grype exits 1 when vulnerabilities are found (strict mode off = 0)
            stderr = result.stderr.decode(errors="replace").strip()
            _log.warning("grype exited %d for %s%s",
                         result.returncode, target,
                         f": {stderr[:200]}" if stderr else "")
            return []

        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("grype output parse error for %s: %s", target, exc)
            return []

        return data.get("matches") or []

    return []  # unreachable but satisfies type checker


def _match_to_finding(
    match: dict[str, Any],
    scan_target: str,
    image_locations: list[str] | None = None,
) -> dict[str, Any]:
    """Convert a single Grype match dict to the standard nuguard finding shape.

    *image_locations* is set only for container-image scans (not the SBOM
    dependency scan) — the Dockerfile path(s) where ``scan_target`` (the image
    ref) is declared, when that evidence exists (see ``container_images.py``).
    """
    vuln     = match.get("vulnerability") or {}
    artifact = match.get("artifact") or {}

    advisory_id = vuln.get("id", "")
    severity_raw = vuln.get("severity", "Unknown").lower()
    severity = _SEV_MAP.get(severity_raw, "UNKNOWN")

    # CVE aliases — present in relatedVulnerabilities
    cve_ids: list[str] = []
    for rv in (match.get("relatedVulnerabilities") or []):
        rid = rv.get("id", "")
        if rid.upper().startswith("CVE-"):
            cve_ids.append(rid.upper())

    # Fix versions
    fix_versions: list[str] = (vuln.get("fix") or {}).get("versions") or []
    affected = (
        f"<{fix_versions[0]}" if fix_versions else "see advisory"
    )

    dep_name    = artifact.get("name", "")
    dep_version = artifact.get("version", "")
    purl        = artifact.get("purl", f"pkg:{dep_name}@{dep_version}")

    adv_url = (vuln.get("dataSource") or
               f"https://osv.dev/vulnerability/{advisory_id}")

    # grype's top-level vulnerability.description is frequently null; the
    # richer NVD/vendor text usually lives on a relatedVulnerabilities entry
    # instead (matched by GHSA/OSV data sources during vuln matching).
    description = vuln.get("description") or next(
        (rv.get("description") for rv in (match.get("relatedVulnerabilities") or [])
         if rv.get("description")),
        None,
    ) or advisory_id

    result = {
        "dep_name":         dep_name,
        "dep_version":      dep_version,
        "purl":             purl,
        "advisory_id":      advisory_id,
        "cve_ids":          cve_ids,
        "summary":          description,
        "severity":         severity,
        "affected_versions": affected,
        "url":              adv_url,
        "source":           "grype",
        "scan_target":      scan_target,
    }
    if image_locations is not None:
        result["image_ref"] = scan_target
        result["image_locations"] = image_locations
    return result


def query_grype_sbom(
    sbom_dict: dict[str, Any],
    timeout: float = 300.0,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """Scan package dependencies in *sbom_dict* via Grype using a CycloneDX BOM.

    The SBOM is serialised to CycloneDX JSON, written to a temp file, then
    passed to ``grype sbom:<path>``.  Returns an empty list if grype is not
    installed or the scan produces no output.
    """
    if _grype_path() is None:
        return []

    # Build CycloneDX JSON from the SBOM deps
    try:
        from nuguard.analysis._cdx import sbom_dict_to_cyclonedx  # noqa: PLC0415
        cdx     = sbom_dict_to_cyclonedx(sbom_dict)
        if not cdx.get("components"):
            _log.debug("grype: no package deps in SBOM — skipping sbom scan")
            return []
        cdx_str = json.dumps(cdx)
    except Exception as exc:
        _log.warning("grype: failed to build CycloneDX BOM: %s", exc)
        return []

    with tempfile.NamedTemporaryFile(
        suffix=".cdx.json", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(cdx_str)
        tmp_path = tmp.name

    try:
        matches = _run_grype(f"sbom:{tmp_path}", timeout=timeout, max_retries=max_retries)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    findings = [_match_to_finding(m, "sbom") for m in matches]
    _log.info("grype sbom scan: %d finding(s)", len(findings))
    return findings


def query_grype_images(
    container_nodes: list[dict[str, Any]],
    timeout: float = 300.0,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """Scan container images referenced in *container_nodes* via Grype.

    *container_nodes* should be SBOM ``nodes`` with
    ``component_type == "CONTAINER_IMAGE"``.  The image reference is derived
    from ``metadata.base_image`` (full ref) or composed from
    ``metadata.image_name`` + ``metadata.image_tag``.

    Returns an empty list if grype is not installed or no valid image refs
    are found.
    """
    if _grype_path() is None:
        return []

    from nuguard.analysis.container_images import resolve_container_images  # noqa: PLC0415
    locations_by_ref = resolve_container_images(container_nodes)

    if not locations_by_ref:
        _log.debug("grype: no CONTAINER_IMAGE refs found in SBOM nodes")
        return []

    sorted_refs = sorted(locations_by_ref)
    all_findings: list[dict[str, Any]] = []
    # Each image scan is an independent, blocking `grype <ref>` subprocess —
    # run them concurrently instead of one at a time.
    with ThreadPoolExecutor(max_workers=min(_IMAGE_SCAN_CONCURRENCY, len(sorted_refs))) as pool:
        future_to_ref = {}
        for ref in sorted_refs:
            _log.info("grype: scanning container image %s", ref)
            future_to_ref[pool.submit(_run_grype, ref, timeout, max_retries)] = ref
        for future in as_completed(future_to_ref):
            ref = future_to_ref[future]
            matches = future.result()
            locations = locations_by_ref[ref]
            all_findings.extend(_match_to_finding(m, ref, locations) for m in matches)

    _log.info("grype image scan(s): %d total finding(s) across %d image(s)",
              len(all_findings), len(locations_by_ref))
    return all_findings
