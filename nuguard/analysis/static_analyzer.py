"""Orchestrate all static SBOM detectors.

The ``StaticAnalyzer`` runs the full analysis pipeline against a deserialized
AI-SBOM document and returns a list of ``Finding`` objects.

Pipeline
--------
1. **NGA structural rules** (``NgaRulesPlugin``) — offline, deterministic;
   each finding is annotated with MITRE ATLAS technique IDs from the
   ``NGA_TO_ATLAS`` mapping table.
2. **OSV dependency CVEs** — network; skipped gracefully on failure.
3. **Grype CVEs** — subprocess; skipped gracefully when grype is not on PATH.
4. **Checkov IaC scans** — subprocess; skipped when checkov absent or no IaC
   paths found.  Requires ``source_path`` or IaC paths in SBOM node metadata.
5. **Trivy** — subprocess; skipped when trivy absent or no scan targets found.
6. **Semgrep** — subprocess; skipped when semgrep absent or no source paths.
7. **MITRE ATLAS native checks** (NC-001..004) — offline graph checks that are
   not already covered by NGA rules (e.g. NC-003: MODEL→DEPLOYMENT without
   AUTH).  ``skip_nga=True`` is passed so the atlas annotator does not re-run
   NGA rules and produce duplicate findings.

Each step's raw finding dicts are converted to ``nuguard.models.finding.Finding``
objects before being returned.  Severity values are normalised to the
``Severity`` enum (unknown/unmapped → ``LOW``).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
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
        Run MITRE ATLAS native graph checks (NC-001..004).  NGA findings are
        always annotated with ATLAS technique IDs via the ``NGA_TO_ATLAS``
        lookup; this flag controls only the additional native checks.
        Defaults to ``True``.
    enable_osv:
        Run OSV dependency CVE scan.  Defaults to ``True``.
    enable_grype:
        Run Grype container/SBOM CVE scan.  Defaults to ``True``.
    enable_checkov:
        Run Checkov IaC scan.  Defaults to ``True`` (skipped automatically
        when checkov is not installed or no IaC paths are found).
    enable_trivy:
        Run Trivy container/fs scan.  Defaults to ``True`` (skipped when
        trivy is not installed or no scan targets are found).
    enable_semgrep:
        Run Semgrep source code scan with bundled AI-security rules.
        Defaults to ``True`` (skipped when semgrep is not installed).
    source_path:
        Optional filesystem path to the application source directory.
        When provided it is injected into SBOM node metadata so that
        Checkov, Trivy, and Semgrep can locate files to scan.
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
        enable_checkov: bool = True,
        enable_trivy: bool = True,
        enable_semgrep: bool = True,
        source_path: Path | None = None,
        atlas_config: dict[str, Any] | None = None,
        min_severity: Severity = Severity.LOW,
    ) -> None:
        self.enable_atlas   = enable_atlas
        self.enable_osv     = enable_osv
        self.enable_grype   = enable_grype
        self.enable_checkov = enable_checkov
        self.enable_trivy   = enable_trivy
        self.enable_semgrep = enable_semgrep
        self.source_path    = source_path
        self.atlas_config   = atlas_config or {}
        self.min_severity   = min_severity
        # Populated by analyze(); maps tool name → status string and optional detail
        self.tool_status: dict[str, dict[str, str]] = {}

    def analyze(self, doc: AiSbomDocument) -> list[Finding]:
        """Run all detectors and return a list of ``Finding`` objects.

        The SBOM document is serialised to its dict representation so that all
        analysis plugins receive a plain ``dict`` (their common interface).
        """
        self.tool_status = {}
        sbom_dict = doc.model_dump()

        # Inject source_path into every SBOM node's metadata so M1 plugins
        # (Checkov, Trivy, Semgrep) can discover files without requiring each
        # SBOM node to carry an explicit source_path field.
        if self.source_path:
            sbom_dict = _inject_source_path(sbom_dict, self.source_path)

        # Config dict forwarded to all M1 plugins so they can fall back to
        # source_path even when SBOM nodes don't carry it explicitly.
        m1_config: dict[str, Any] = {}
        if self.source_path:
            m1_config["source_path"] = str(self.source_path)

        all_findings: list[Finding] = []

        # Step 1: NGA structural rules (annotated with ATLAS techniques inline)
        nga = self._run_nga(sbom_dict)
        all_findings.extend(nga)
        self.tool_status["nga-rules"] = {
            "status": "ok", "findings": str(len(nga)),
        }

        # Step 2: OSV dependency CVEs
        if self.enable_osv:
            osv = self._run_osv(sbom_dict)
            all_findings.extend(osv)
            self.tool_status["osv"] = {"status": "ok", "findings": str(len(osv))}
        else:
            self.tool_status["osv"] = {"status": "disabled", "findings": "0"}

        # Step 3: Grype CVEs (subprocess; skipped gracefully when absent)
        if self.enable_grype:
            import shutil  # noqa: PLC0415
            if shutil.which("grype"):
                grype = self._run_grype(sbom_dict)
                all_findings.extend(grype)
                self.tool_status["grype"] = {"status": "ok", "findings": str(len(grype))}
            else:
                self.tool_status["grype"] = {
                    "status": "skipped", "findings": "0",
                    "reason": "grype not installed",
                }
        else:
            self.tool_status["grype"] = {"status": "disabled", "findings": "0"}

        # Step 4: Checkov IaC scan
        # _run_m1 sets tool_status to "skipped"/"error" when the plugin bails
        # early; only overwrite with "ok" if it did not do so already.
        if self.enable_checkov:
            checkov = self._run_m1("checkov", sbom_dict, m1_config)
            all_findings.extend(checkov)
            if self.tool_status.get("checkov", {}).get("status") not in ("skipped", "error"):
                self.tool_status["checkov"] = {"status": "ok", "findings": str(len(checkov))}
        else:
            self.tool_status["checkov"] = {"status": "disabled", "findings": "0"}

        # Step 5: Trivy container/fs scan
        if self.enable_trivy:
            trivy = self._run_m1("trivy", sbom_dict, m1_config)
            all_findings.extend(trivy)
            if self.tool_status.get("trivy", {}).get("status") not in ("skipped", "error"):
                self.tool_status["trivy"] = {"status": "ok", "findings": str(len(trivy))}
        else:
            self.tool_status["trivy"] = {"status": "disabled", "findings": "0"}

        # Step 6: Semgrep source code scan
        if self.enable_semgrep:
            semgrep = self._run_m1("semgrep", sbom_dict, m1_config)
            all_findings.extend(semgrep)
            if self.tool_status.get("semgrep", {}).get("status") not in ("skipped", "error"):
                self.tool_status["semgrep"] = {"status": "ok", "findings": str(len(semgrep))}
        else:
            self.tool_status["semgrep"] = {"status": "disabled", "findings": "0"}

        # Step 7: MITRE ATLAS native graph checks (NC-001..004)
        # skip_nga=True so the annotator does not re-run NGA rules; Step 1
        # already annotated NGA findings with ATLAS techniques directly.
        if self.enable_atlas:
            atlas = self._run_atlas_native(sbom_dict)
            all_findings.extend(atlas)
            self.tool_status["atlas"] = {"status": "ok", "findings": str(len(atlas))}
        else:
            self.tool_status["atlas"] = {"status": "disabled", "findings": "0"}

        # Filter by minimum severity
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
        """Run NgaRulesPlugin (NGA-001…NGA-019) and annotate with ATLAS techniques."""
        try:
            from nuguard.analysis._atlas_data import NGA_TO_ATLAS  # noqa: PLC0415
            from nuguard.analysis.plugins.nga_rules import NgaRulesPlugin  # noqa: PLC0415

            plugin = NgaRulesPlugin()
            # provider="nga-rules" skips the OSV/Grype phases inside the plugin;
            # those are run separately by _run_osv() and _run_grype().
            result = plugin.run(sbom_dict, {"provider": "nga-rules"})

            findings: list[Finding] = []
            for raw in list(result.findings or []):
                rule_id = raw.get("rule_id", "")
                # Annotate with ATLAS techniques inline from the lookup table so
                # the atlas annotator does not need to re-run NGA rules.
                if rule_id in NGA_TO_ATLAS:
                    raw = dict(raw)
                    raw["atlas"] = {
                        "techniques": [
                            {"technique_id": tid, "confidence": conf}
                            for tid, conf in NGA_TO_ATLAS[rule_id]
                        ]
                    }
                findings.append(_raw_to_finding(raw, "nga"))

            _log.info("NGA rules: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("NGA rules pass failed: %s", exc)
            return []

    def _run_osv(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run OSV querybatch against SBOM deps."""
        try:
            from nuguard.analysis.osv_client import query_osv  # noqa: PLC0415
            from nuguard.analysis.plugins.nga_rules import _osv_to_finding  # noqa: PLC0415

            deps = list(sbom_dict.get("deps") or [])
            if not deps:
                return []
            # _osv_to_finding converts raw OSV dicts to the standard finding shape,
            # including a remediation field with the upgrade instruction.
            raw_list = [_osv_to_finding(r) for r in query_osv(deps)]
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
                query_grype_images,
                query_grype_sbom,
            )
            from nuguard.analysis.plugins.nga_rules import _grype_to_finding  # noqa: PLC0415

            raw_list = [_grype_to_finding(r) for r in query_grype_sbom(sbom_dict)]
            container_nodes = [
                n for n in (sbom_dict.get("nodes") or [])
                if (n.get("component_type") or "").upper() == "CONTAINER_IMAGE"
            ]
            if container_nodes:
                raw_list += [_grype_to_finding(r) for r in query_grype_images(container_nodes)]
            findings = [_raw_to_finding(r, "grype") for r in raw_list]
            _log.info("Grype scan: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("Grype scan failed: %s", exc)
            return []

    def _run_m1(
        self, tool: str, sbom_dict: dict[str, Any], config: dict[str, Any] | None = None
    ) -> list[Finding]:
        """Run a named M1 scanner plugin (checkov | trivy | semgrep)."""
        cfg = config or {}
        try:
            plugin: Any
            if tool == "checkov":
                from nuguard.analysis.plugins.checkov_scanner import (
                    CheckovScannerPlugin,  # noqa: PLC0415
                )
                plugin = CheckovScannerPlugin()
            elif tool == "trivy":
                from nuguard.analysis.plugins.trivy_scanner import (
                    TrivyScannerPlugin,  # noqa: PLC0415
                )
                plugin = TrivyScannerPlugin()
            elif tool == "semgrep":
                from nuguard.analysis.plugins.semgrep_scanner import (
                    SemgrepScannerPlugin,  # noqa: PLC0415
                )
                plugin = SemgrepScannerPlugin()
            else:
                return []

            result = plugin.run(sbom_dict, cfg)
            if result.status == "skipped":
                _log.info("%s: skipped (%s)", tool, result.message)
                # Update tool_status with the skip reason
                self.tool_status[tool] = {
                    "status": "skipped",
                    "findings": "0",
                    "reason": result.message or "skipped",
                }
                return []
            if result.status == "error":
                self.tool_status[tool] = {
                    "status": "error",
                    "findings": "0",
                    "reason": result.message or "error",
                }
                return []
            raw_list = list(result.findings or [])
            findings = [_raw_to_finding(r, tool) for r in raw_list]
            _log.info("%s: %d finding(s)", tool, len(findings))
            return findings
        except Exception as exc:
            _log.warning("%s scan failed: %s", tool, exc)
            self.tool_status[tool] = {
                "status": "error",
                "findings": "0",
                "reason": str(exc),
            }
            return []

    def _run_atlas_native(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run only the MITRE ATLAS native graph checks (NC-001..004).

        ``skip_nga=True`` prevents the annotator from re-running NGA rules;
        NGA findings were already annotated with ATLAS techniques in
        ``_run_nga()``.
        """
        try:
            from nuguard.analysis.plugins.atlas_annotator import (
                AtlasAnnotatorPlugin,  # noqa: PLC0415
            )

            plugin = AtlasAnnotatorPlugin()
            config = {**self.atlas_config, "skip_nga": True}
            result = plugin.run(sbom_dict, config)
            raw_list: list[dict[str, Any]] = list(result.details.get("findings") or [])
            findings = [_raw_to_finding(r, "atlas") for r in raw_list]
            _log.info("ATLAS native checks: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("ATLAS native checks failed: %s", exc)
            return []


def _inject_source_path(sbom_dict: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """Return a copy of sbom_dict with source_path injected into node metadata.

    M1 plugins (Checkov, Trivy, Semgrep) look for ``metadata.source_path`` on
    SBOM nodes to find files to scan.  When a SBOM was generated from a remote
    repo the paths are relative; this helper makes them absolute by prepending
    ``source_path``.
    """
    import copy
    sbom = copy.deepcopy(sbom_dict)
    src = str(source_path)
    for node in sbom.get("nodes") or []:
        meta = node.setdefault("metadata", {})
        if not meta.get("source_path"):
            meta["source_path"] = src
    return sbom
