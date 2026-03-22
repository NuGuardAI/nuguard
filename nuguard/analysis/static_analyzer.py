"""Orchestrate all static SBOM detectors.

The ``StaticAnalyzer`` runs the full analysis pipeline against a deserialized
AI-SBOM document and returns a list of ``Finding`` objects.

Pipeline
--------
1. **NGA structural rules** (``NgaRulesPlugin``) — offline, deterministic
2. **OSV dependency CVEs** — network; skipped gracefully on failure
3. **Grype CVEs** — subprocess; skipped gracefully when grype is not on PATH
4. **MITRE ATLAS annotation** (``AtlasAnnotatorPlugin``) — offline + optional LLM

Each step's raw finding dicts are converted to ``nuguard.models.finding.Finding``
objects before being returned.  Severity values are normalised to the
``Severity`` enum (unknown/unmapped → ``LOW``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from nuguard.models.finding import Finding, Severity
from nuguard.models.sbom import AiSbomDocument

_log = logging.getLogger("analysis.static_analyzer")

# Severity normalisation map: raw string → Severity enum
_SEV_MAP: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "high":     Severity.HIGH,
    "medium":   Severity.MEDIUM,
    "low":      Severity.LOW,
    "info":     Severity.INFO,
    "unknown":  Severity.LOW,
    "none":     Severity.INFO,
    "negligible": Severity.LOW,
}


def _to_severity(raw: str) -> Severity:
    return _SEV_MAP.get(str(raw).lower(), Severity.LOW)


def _raw_to_finding(raw: dict[str, Any], source: str) -> Finding:
    """Convert a raw finding dict (from any plugin) to a ``Finding`` model."""
    rule_id = raw.get("rule_id") or raw.get("advisory_id") or "UNKNOWN"
    title   = raw.get("title") or raw.get("summary") or rule_id
    desc    = raw.get("description") or raw.get("summary") or ""
    sev     = _to_severity(raw.get("severity", "LOW"))

    affected_parts: list[str] = []
    if raw.get("affected"):
        parts = raw["affected"]
        affected_parts = parts if isinstance(parts, list) else [str(parts)]
    elif raw.get("dep_name"):
        affected_parts = [raw["dep_name"]]

    references: list[str] = []
    url = raw.get("url") or raw.get("advisory_url") or raw.get("dataSource")
    if url:
        references.append(str(url))

    # Collect ATLAS technique IDs from the atlas block if present
    atlas_block = raw.get("atlas") or {}
    atlas_techniques = [
        t.get("technique_id", "")
        for t in (atlas_block.get("techniques") or [])
        if t.get("technique_id")
    ]
    mitre_atlas = ", ".join(atlas_techniques) if atlas_techniques else None

    return Finding(
        finding_id=f"{source}-{rule_id}-{uuid.uuid4().hex[:8]}",
        title=title,
        severity=sev,
        description=desc,
        affected_component=", ".join(affected_parts) if affected_parts else None,
        remediation=raw.get("remediation"),
        references=references,
        mitre_atlas_technique=mitre_atlas,
        evidence=raw.get("evidence"),
    )


class StaticAnalyzer:
    """Orchestrate all static analysis detectors against an AI-SBOM.

    Parameters
    ----------
    enable_atlas:
        Run MITRE ATLAS annotation pass (Pass 2 of the pipeline).
        Defaults to ``True``.
    enable_osv:
        Run OSV dependency CVE scan.  Defaults to ``True``.
    enable_grype:
        Run Grype container/SBOM CVE scan.  Defaults to ``True``.
    atlas_config:
        Extra config dict passed to ``AtlasAnnotatorPlugin.run()``.
        Set ``{"llm": True}`` to enable LLM enrichment.
    min_severity:
        Drop findings below this severity from the returned list.
        Defaults to ``Severity.LOW`` (include everything).
    """

    def __init__(
        self,
        enable_atlas: bool = True,
        enable_osv: bool = True,
        enable_grype: bool = True,
        atlas_config: dict[str, Any] | None = None,
        min_severity: Severity = Severity.LOW,
    ) -> None:
        self.enable_atlas  = enable_atlas
        self.enable_osv    = enable_osv
        self.enable_grype  = enable_grype
        self.atlas_config  = atlas_config or {}
        self.min_severity  = min_severity

    def analyze(self, doc: AiSbomDocument) -> list[Finding]:
        """Run all detectors and return a list of ``Finding`` objects.

        The SBOM document is serialised to its dict representation so that all
        analysis plugins receive a plain ``dict`` (their common interface).
        """
        sbom_dict = doc.model_dump()
        all_findings: list[Finding] = []

        # ------------------------------------------------------------------
        # Step 1: NGA structural rules
        # ------------------------------------------------------------------
        all_findings.extend(self._run_nga(sbom_dict))

        # ------------------------------------------------------------------
        # Step 2: OSV dependency CVEs
        # ------------------------------------------------------------------
        if self.enable_osv:
            all_findings.extend(self._run_osv(sbom_dict))

        # ------------------------------------------------------------------
        # Step 3: Grype CVEs
        # ------------------------------------------------------------------
        if self.enable_grype:
            all_findings.extend(self._run_grype(sbom_dict))

        # ------------------------------------------------------------------
        # Step 4: MITRE ATLAS annotation
        # ------------------------------------------------------------------
        if self.enable_atlas:
            all_findings.extend(self._run_atlas(sbom_dict))

        # ------------------------------------------------------------------
        # Filter by minimum severity
        # ------------------------------------------------------------------
        sev_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
                     Severity.LOW: 3, Severity.INFO: 4}
        min_rank = sev_order.get(self.min_severity, 4)
        all_findings = [f for f in all_findings if sev_order.get(f.severity, 99) <= min_rank]

        _log.info("static analysis complete: %d finding(s)", len(all_findings))
        return all_findings

    # ------------------------------------------------------------------
    # Internal runner helpers
    # ------------------------------------------------------------------

    def _run_nga(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run NgaRulesPlugin (structural NGA-001 … NGA-019)."""
        try:
            from nuguard.analysis.plugins.nga_rules import NgaRulesPlugin  # noqa: PLC0415
            plugin = NgaRulesPlugin()
            result = plugin.run(sbom_dict, {})
            raw_list: list[dict[str, Any]] = list(result.details.get("findings") or [])
            findings = [_raw_to_finding(r, "nga") for r in raw_list]
            _log.info("NGA rules: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("NGA rules pass failed: %s", exc)
            return []

    def _run_osv(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run OSV querybatch against SBOM deps."""
        try:
            from nuguard.analysis.osv_client import query_osv  # noqa: PLC0415
            deps = list(sbom_dict.get("deps") or [])
            if not deps:
                return []
            raw_list = query_osv(deps)
            findings = [_raw_to_finding(r, "osv") for r in raw_list]
            _log.info("OSV scan: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("OSV scan failed: %s", exc)
            return []

    def _run_grype(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run Grype SBOM + container image scans."""
        try:
            from nuguard.analysis.grype_client import (  # noqa: PLC0415
                query_grype_sbom,
                query_grype_images,
            )
            raw_list: list[dict[str, Any]] = query_grype_sbom(sbom_dict)
            container_nodes = [
                n for n in (sbom_dict.get("nodes") or [])
                if (n.get("component_type") or "").upper() == "CONTAINER_IMAGE"
            ]
            if container_nodes:
                raw_list += query_grype_images(container_nodes)
            findings = [_raw_to_finding(r, "grype") for r in raw_list]
            _log.info("Grype scan: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("Grype scan failed: %s", exc)
            return []

    def _run_atlas(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run MITRE ATLAS annotation pass."""
        try:
            from nuguard.analysis.plugins.atlas_annotator import AtlasAnnotatorPlugin  # noqa: PLC0415
            plugin = AtlasAnnotatorPlugin()
            result = plugin.run(sbom_dict, self.atlas_config)
            raw_list: list[dict[str, Any]] = list(result.details.get("findings") or [])
            findings = [_raw_to_finding(r, "atlas") for r in raw_list]
            _log.info("ATLAS annotation: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("ATLAS annotation pass failed: %s", exc)
            return []
