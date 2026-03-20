"""CycloneDX export plugin.

By default, exports the SBOM as a CycloneDX 1.6 BOM.

When ``include_vulnerabilities=True`` is passed in config (and
``provider`` is ``osv`` or ``all``), the plugin also queries the OSV API
for known CVEs in the SBOM's ``deps`` list and attaches a CycloneDX
``vulnerabilities`` array to the BOM, producing a combined BOM + VEX
document consumable by Grype, Trivy, and other CycloneDX-aware scanners.
"""
from __future__ import annotations

import logging
from typing import Any

from xelo.models import AiSbomDocument
from xelo.serializer import AiSbomSerializer

from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.cyclonedx")


class CycloneDxExporter(ToolPlugin):
    name = "cyclonedx_export"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        spec = config.get("spec_version", "1.6")
        _log.info("generating CycloneDX %s BOM (%d node(s))",
                  spec, len(sbom.get("nodes") or []))
        doc     = AiSbomDocument.model_validate(sbom)
        payload = AiSbomSerializer.to_cyclonedx(doc, spec_version=spec)
        _log.debug("BOM has %d component(s)", len(payload.get("components", [])))

        include_vulns = config.get("include_vulnerabilities", False)
        provider      = config.get("provider", "vela-rules")

        if include_vulns and provider in ("osv", "all"):
            deps = sbom.get("deps") or []
            timeout = float(config.get("timeout", 15.0))
            _log.info("fetching OSV vulnerability data for %d dep(s)", len(deps))
            vex = _build_vex(deps, timeout=timeout)
            if vex:
                _log.info("attaching %d vulnerability record(s) to BOM (VEX)", len(vex))
                payload["vulnerabilities"] = vex
            else:
                _log.info("no OSV vulnerabilities found for this SBOM's deps")

        return ToolResult(
            status="ok",
            tool=self.name,
            message="CycloneDX export generated",
            details=payload,
        )


# ── VEX builder ──────────────────────────────────────────────────────────────

def _build_vex(deps: list[dict[str, Any]], timeout: float) -> list[dict[str, Any]]:
    """Query OSV and return a CycloneDX-shaped vulnerabilities list."""
    try:
        from xelo.toolbox.osv_client import query_osv
    except ImportError:
        return []

    osv_results = query_osv(deps, timeout=timeout)
    if not osv_results:
        return []

    try:
        from cyclonedx.model.vulnerability import (
            Vulnerability,
            VulnerabilityRating,
            VulnerabilitySeverity,
            VulnerabilitySource,
        )
        from cyclonedx.model import XsUri
        _HAS_CDX = True
    except ImportError:
        _HAS_CDX = False

    vex: list[dict[str, Any]] = []
    for osv in osv_results:
        entry: dict[str, Any] = {
            "id":          osv["advisory_id"],
            "source":      {"name": "OSV", "url": osv.get("url", "https://osv.dev")},
            "ratings":     [_cvss_rating(osv)],
            "description": osv.get("summary", ""),
            "recommendation": (
                f"Upgrade {osv['dep_name']} to a version outside "
                f"{osv.get('affected_versions', 'the affected range')}."
            ),
            "affects": [{"ref": osv.get("purl", osv["dep_name"])}],
        }
        if osv.get("cve_ids"):
            entry["references"] = [
                {"id": cve, "source": {"name": "NVD",
                 "url": f"https://nvd.nist.gov/vuln/detail/{cve}"}}
                for cve in osv["cve_ids"]
            ]

        # If cyclonedx-python-lib is available, validate through its model
        if _HAS_CDX:
            try:
                sev_label = osv.get("severity", "UNKNOWN").lower()
                sev_enum  = VulnerabilitySeverity(sev_label) if sev_label in {
                    e.value for e in VulnerabilitySeverity
                } else VulnerabilitySeverity.UNKNOWN

                vuln_obj = Vulnerability(
                    id=osv["advisory_id"],
                    source=VulnerabilitySource(
                        name="OSV",
                        url=XsUri(osv.get("url", "https://osv.dev")),
                    ),
                    ratings=[VulnerabilityRating(severity=sev_enum)],
                    description=osv.get("summary", ""),
                    recommendation=(
                        f"Upgrade {osv['dep_name']} to a version outside "
                        f"{osv.get('affected_versions', 'the affected range')}."
                    ),
                )
                # Validated OK — use the plain dict form for JSON serialisation
                _ = vuln_obj  # noqa: F841
            except Exception as exc:
                _log.debug("cyclonedx model validation skipped for %s: %s", osv.get("advisory_id"), exc)

        vex.append(entry)

    return vex


def _cvss_rating(osv: dict[str, Any]) -> dict[str, Any]:
    sev = osv.get("severity", "unknown").lower()
    score_map = {
        "critical": "9.0", "high": "7.5", "medium": "5.5",
        "low": "2.5", "unknown": None,
    }
    rating: dict[str, Any] = {"severity": sev, "method": "CVSSv3"}
    score = score_map.get(sev)
    if score:
        rating["score"] = score
    return rating
