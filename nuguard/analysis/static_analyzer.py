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

Concurrency
-----------
Steps 2-6 (OSV, Grype, Checkov, Trivy, Semgrep) are independent of each
other's output and each blocks on a subprocess or network call, so
``analyze()`` runs them concurrently via ``asyncio.gather`` (each wrapped in
``asyncio.to_thread`` since the underlying clients are synchronous) instead of
one after another — wall-clock time becomes roughly the slowest single step
instead of their sum. NGA rules (step 1) still run first and synchronously
since ATLAS's ``skip_nga`` path assumes NGA already ran; ATLAS native checks
and supply-chain stay sequential after the gather.
"""

from __future__ import annotations

import asyncio
import functools
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from nuguard.common.control_mappings.cicd import cicd_refs_for_rule
from nuguard.common.control_mappings.owasp import owasp_refs_for_rule
from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, FindingKind, FindingLocation, Severity
from nuguard.models.tool_run import ToolRun, ToolRunStatus
from nuguard.sbom.models import AiSbomDocument

_log = get_logger("analysis.static_analyzer")

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


# Display order of tools in tool_status / tool_runs.
TOOL_ORDER: tuple[str, ...] = (
    "nga-rules", "osv", "grype", "checkov", "trivy", "semgrep", "atlas", "supply-chain",
)

# _raw_to_finding source label → ToolRun.tool name
_SOURCE_TOOL: dict[str, str] = {"nga": "nga-rules"}

# Default finding kind per tool (refined per finding in _projection())
_DEFAULT_KIND: dict[str, FindingKind] = {
    "nga-rules": "posture",
    "atlas": "posture",
    "osv": "dependency",
    "grype": "dependency",
    "checkov": "iac",
    "trivy": "dependency",
    "semgrep": "code",
    "supply-chain": "supply_chain",
}

# Legacy tool_status["status"] value for each typed status (the CLI and older
# integrations read "ok" / "error" / "skipped" / "disabled" only).
_LEGACY_STATUS: dict[ToolRunStatus, str] = {
    ToolRunStatus.OK: "ok",
    ToolRunStatus.FAILED: "error",
    ToolRunStatus.TIMEOUT: "error",
    ToolRunStatus.SKIPPED: "skipped",
    ToolRunStatus.NOT_APPLICABLE: "skipped",
    ToolRunStatus.DISABLED: "disabled",
}

_ABS_PATH_RE = re.compile(r"(?<![\w.)])(?:[A-Za-z]:)?[\\/][^\s'\"]+")


def safe_reason(message: str | None, default: str = "error") -> str:
    """Short, user-safe reason text: no absolute paths, no multi-line stderr."""
    text = (message or "").strip().splitlines()[0] if (message or "").strip() else default
    text = _ABS_PATH_RE.sub("<path>", text)
    return text[:160]


@functools.lru_cache(maxsize=None)
def tool_version(tool: str) -> str | None:
    """Best-effort version of the binary or rule pack behind *tool* (cached per process)."""
    if tool in ("nga-rules", "atlas", "supply-chain"):
        from nuguard import __version__  # noqa: PLC0415

        return f"nuguard {__version__}"
    if tool == "osv":
        return "api.osv.dev/v1"
    commands = {
        "semgrep": ["semgrep", "--version"],
        "checkov": ["checkov", "--version"],
        "trivy": ["trivy", "--version"],
        "grype": ["grype", "version"],
    }
    cmd = commands.get(tool)
    if cmd is None or shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.decode(errors="replace") + "\n" + out.stderr.decode(errors="replace")
    match = re.search(r"\b(\d+\.\d+(?:\.\d+)?(?:[-+.][0-9A-Za-z.]+)?)\b", text)
    return match.group(1) if match else None


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

    # OWASP LLM Top 10 / Agentic Top 10 citations: prefer an explicit reference
    # already carried on the raw finding (e.g. semgrep's YAML `owasp` metadata),
    # falling back to the NGA_TO_OWASP rule_id lookup table.
    owasp_refs = owasp_refs_for_rule(rule_id)
    owasp_llm_ref = raw.get("owasp_llm_ref") or (
        ", ".join(owasp_refs.owasp_llm) if owasp_refs.owasp_llm else None
    )
    owasp_asi_ref = raw.get("owasp_asi_ref") or (
        ", ".join(owasp_refs.owasp_agentic) if owasp_refs.owasp_agentic else None
    )

    # OWASP Top 10 CI/CD Security Risks citation, for NGA-SC-* supply-chain rules only.
    cicd_refs = cicd_refs_for_rule(rule_id)
    owasp_cicd_ref = raw.get("owasp_cicd_ref") or (", ".join(cicd_refs) if cicd_refs else None)

    tool = _SOURCE_TOOL.get(source, source)
    return Finding(
        finding_id=f"{source}-{rule_id}-{uuid.uuid4().hex[:8]}",
        title=title,
        severity=sev,
        description=desc,
        rule_id=None if rule_id == "UNKNOWN" else str(rule_id),
        source_tool=tool,
        kind=raw.get("kind") or _DEFAULT_KIND.get(tool),
        affected_component=", ".join(affected_parts) if affected_parts else None,
        remediation=raw.get("remediation"),
        references=references,
        owasp_llm_ref=owasp_llm_ref,
        owasp_asi_ref=owasp_asi_ref,
        owasp_cicd_ref=owasp_cicd_ref,
        mitre_atlas_technique=mitre_atlas,
        evidence=raw.get("evidence"),
        container_image=raw.get("container_image"),
        container_image_locations=list(raw.get("container_image_locations") or []),
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
    offline:
        Do not use the network: OSV, Grype and Trivy (which need advisory data
        or DB downloads) are reported ``skipped`` rather than run, and
        supply-chain artifact verification is forced off.
    dedupe_posture:
        Merge findings from rules that fire once per model-name string (NGA-002
        sub-check A, NGA-001 data-flow paths) into one finding per SBOM node,
        with the model names listed in ``evidence``.
    """

    def __init__(
        self,
        enable_atlas: bool = True,
        enable_osv: bool = True,
        enable_grype: bool = True,
        enable_checkov: bool = True,
        enable_trivy: bool = True,
        enable_semgrep: bool = True,
        enable_supply_chain: bool = True,
        source_path: Path | None = None,
        atlas_config: dict[str, Any] | None = None,
        min_severity: Severity = Severity.LOW,
        verbose: bool = False,
        grype_timeout: float = 180.0,
        grype_retries: int = 3,
        supply_chain_profile: str = "standard",
        supply_chain_verify_artifacts: str = "off",
        supply_chain_threat_intel_feeds: list[str] | None = None,
        checkov_timeout: float = 120.0,
        checkov_total_timeout: float = 300.0,
        semgrep_timeout: float = 120.0,
        semgrep_total_timeout: float = 300.0,
        offline: bool = False,
        dedupe_posture: bool = False,
    ) -> None:
        from nuguard.analysis.plugins.scanner_runtime import positive_timeout

        self.checkov_timeout = positive_timeout(checkov_timeout)
        self.checkov_total_timeout = positive_timeout(checkov_total_timeout)
        self.semgrep_timeout = positive_timeout(semgrep_timeout)
        self.semgrep_total_timeout = positive_timeout(semgrep_total_timeout)
        self.enable_atlas   = enable_atlas
        self.enable_osv     = enable_osv
        self.enable_grype   = enable_grype
        self.enable_checkov = enable_checkov
        self.enable_trivy   = enable_trivy
        self.enable_semgrep = enable_semgrep
        self.enable_supply_chain = enable_supply_chain
        self.source_path    = source_path
        self.atlas_config   = atlas_config or {}
        self.min_severity   = min_severity
        self.verbose        = verbose
        self.grype_timeout  = grype_timeout
        self.grype_retries  = grype_retries
        self.supply_chain_profile = supply_chain_profile
        self.supply_chain_verify_artifacts = supply_chain_verify_artifacts
        self.supply_chain_threat_intel_feeds = supply_chain_threat_intel_feeds
        self.offline = offline
        self.dedupe_posture = dedupe_posture
        # Populated by analyze(); maps tool name → status string and optional detail
        # (legacy view; tool_runs below is the typed equivalent).
        self.tool_status: dict[str, dict[str, str]] = {}
        self._tool_runs: dict[str, ToolRun] = {}
        # Per-run context for location projection (set in analyze()).
        self._nodes_by_name: dict[str, list[dict[str, Any]]] = {}
        self._dep_files: dict[str, str] = {}
        # Populated when verbose=True; per-rule pass/fail audit for NGA rules
        self.nga_audit: list[dict[str, Any]] = []
        # Per-rule pass/fail audit for supply-chain rules (always populated)
        self.sc_audit: list[dict[str, Any]] = []
        # Accumulated LLM token usage across all plugins (ATLAS LLM enrichment)
        from nuguard.models.token_usage import TokenUsage  # noqa: PLC0415
        self.token_usage: "TokenUsage" = TokenUsage()

    @property
    def tool_runs(self) -> list[ToolRun]:
        """Typed per-tool outcomes of the last ``analyze()`` call, in display order."""
        order = {t: i for i, t in enumerate(TOOL_ORDER)}
        return sorted(self._tool_runs.values(), key=lambda r: order.get(r.tool, len(order)))

    def _set_status(
        self,
        tool: str,
        status: ToolRunStatus,
        findings: int = 0,
        reason: str | None = None,
    ) -> None:
        """Record *tool*'s outcome in both the typed and the legacy view."""
        if status not in (ToolRunStatus.OK, ToolRunStatus.DISABLED):
            reason = safe_reason(reason, default=status.value)
        else:
            reason = None
        self._tool_runs[tool] = ToolRun(
            tool=tool, status=status, findings=findings, reason=reason
        )
        legacy = {"status": _LEGACY_STATUS[status], "findings": str(findings)}
        if reason:
            legacy["reason"] = reason
        self.tool_status[tool] = legacy

    def _recorded(self, tool: str) -> bool:
        return tool in self._tool_runs

    def _finish_run(self, tool: str, started: float) -> None:
        """Attach duration and version to a recorded tool run."""
        run = self._tool_runs.get(tool)
        if run is None:
            return
        update: dict[str, Any] = {"duration_ms": int((time.monotonic() - started) * 1000)}
        if run.status in (ToolRunStatus.OK, ToolRunStatus.FAILED, ToolRunStatus.TIMEOUT):
            update["version"] = tool_version(tool)
        self._tool_runs[tool] = run.model_copy(update=update)

    async def analyze(self, doc: AiSbomDocument) -> list[Finding]:
        """Run all detectors and return a list of ``Finding`` objects.

        The SBOM document is serialised to its dict representation so that all
        analysis plugins receive a plain ``dict`` (their common interface).
        """
        # Pre-seed tool_status keys in the report's display order so the
        # concurrent dispatch below (step 2-6, completion order is
        # nondeterministic) can't scramble the Tool Coverage table — a later
        # `self.tool_status[key] = {...}` updates the value in place without
        # moving the key (Python dicts preserve first-insertion order).
        self.tool_status = {tool: {} for tool in TOOL_ORDER}
        self._tool_runs = {}
        sbom_dict = doc.model_dump()
        self._index_sbom(doc.model_dump(mode="json"))

        # Inject source_path into every SBOM node's metadata so M1 plugins
        # (Checkov, Trivy, Semgrep) can discover files without requiring each
        # SBOM node to carry an explicit source_path field.
        if self.source_path:
            sbom_dict = _inject_source_path(sbom_dict, self.source_path)

        # Config dict forwarded to all M1 plugins so they can fall back to
        # source_path even when SBOM nodes don't carry it explicitly.
        m1_config: dict[str, Any] = {}
        m1_config["checkov_timeout"] = self.checkov_timeout
        m1_config["checkov_total_timeout"] = self.checkov_total_timeout
        m1_config["semgrep_timeout"] = self.semgrep_timeout
        m1_config["semgrep_total_timeout"] = self.semgrep_total_timeout
        if self.source_path:
            m1_config["source_path"] = str(self.source_path)
        m1_config["supply_chain_profile"] = self.supply_chain_profile
        m1_config["supply_chain_verify_artifacts"] = (
            "off" if self.offline else self.supply_chain_verify_artifacts
        )
        m1_config["supply_chain_threat_intel_feeds"] = self.supply_chain_threat_intel_feeds

        # Build the analysis graph once; injected into NGA rules and ATLAS native
        # checks so they can do indexed traversal instead of O(n²) flat scans.
        from nuguard.analysis.graph import AnalysisGraph  # noqa: PLC0415
        sbom_graph = AnalysisGraph(sbom_dict)

        all_findings: list[Finding] = []

        # Step 1: NGA structural rules (annotated with ATLAS techniques inline).
        # Runs first and synchronously — offline/cheap, and ATLAS's skip_nga
        # path (step 7) assumes NGA already ran.
        started = time.monotonic()
        nga = self._run_nga(sbom_dict, sbom_graph=sbom_graph)
        all_findings.extend(nga)
        if not self._recorded("nga-rules"):
            self._set_status("nga-rules", ToolRunStatus.OK, len(nga))
        self._finish_run("nga-rules", started)

        # Steps 2-6: OSV, Grype, Checkov, Trivy, Semgrep are independent of
        # each other's output and each blocks on a subprocess or network call
        # — run them concurrently (thread pool, since the underlying clients
        # are synchronous) instead of one after another. Each _run_* records a
        # non-ok status itself; the wrappers only fill in "ok" when it did not.
        offline_reason = "offline mode: network lookups disabled"

        async def _osv_step() -> list[Finding]:
            if not self.enable_osv:
                self._set_status("osv", ToolRunStatus.DISABLED)
                return []
            if self.offline:
                self._set_status("osv", ToolRunStatus.SKIPPED, reason=offline_reason)
                return []
            osv = await asyncio.to_thread(self._run_osv, sbom_dict)
            if not self._recorded("osv"):
                self._set_status("osv", ToolRunStatus.OK, len(osv))
            return osv

        async def _grype_step() -> list[Finding]:
            if not self.enable_grype:
                self._set_status("grype", ToolRunStatus.DISABLED)
                return []
            if self.offline:
                self._set_status("grype", ToolRunStatus.SKIPPED, reason=offline_reason)
                return []
            if not shutil.which("grype"):
                self._set_status("grype", ToolRunStatus.SKIPPED, reason="grype not installed")
                return []
            grype = await asyncio.to_thread(self._run_grype, sbom_dict)
            if not self._recorded("grype"):
                self._set_status("grype", ToolRunStatus.OK, len(grype))
            return grype

        async def _m1_step(tool: str, enabled: bool) -> list[Finding]:
            if not enabled:
                self._set_status(tool, ToolRunStatus.DISABLED)
                return []
            if tool == "trivy" and self.offline:
                self._set_status(tool, ToolRunStatus.SKIPPED, reason=offline_reason)
                return []
            findings = await asyncio.to_thread(self._run_m1, tool, sbom_dict, m1_config)
            if not self._recorded(tool):
                self._set_status(tool, ToolRunStatus.OK, len(findings))
            return findings

        async def _timed(tool: str, step: Any) -> list[Finding]:
            step_started = time.monotonic()
            try:
                return await step  # type: ignore[no-any-return]
            finally:
                self._finish_run(tool, step_started)

        step_labels = ("osv", "grype", "checkov", "trivy", "semgrep")
        step_results = await asyncio.gather(
            _timed("osv", _osv_step()),
            _timed("grype", _grype_step()),
            _timed("checkov", _m1_step("checkov", self.enable_checkov)),
            _timed("trivy", _m1_step("trivy", self.enable_trivy)),
            _timed("semgrep", _m1_step("semgrep", self.enable_semgrep)),
            return_exceptions=True,
        )
        for label, result in zip(step_labels, step_results):
            if isinstance(result, BaseException):
                # Each _run_* already catches its own errors and records them;
                # this is a defensive fallback for anything unexpected (e.g.
                # a bug in the step wrapper itself) so one failure can't take
                # down the whole gather.
                _log.warning("%s step failed unexpectedly: %s", label, result)
                self._set_status(label, ToolRunStatus.FAILED, reason="unexpected error")
                continue
            all_findings.extend(result)

        # Step 7: MITRE ATLAS native graph checks (NC-001..004)
        # skip_nga=True so the annotator does not re-run NGA rules; Step 1
        # already annotated NGA findings with ATLAS techniques directly.
        started = time.monotonic()
        if self.enable_atlas:
            atlas = await asyncio.to_thread(
                self._run_atlas_native, sbom_dict, sbom_graph=sbom_graph
            )
            all_findings.extend(atlas)
            if not self._recorded("atlas"):
                self._set_status("atlas", ToolRunStatus.OK, len(atlas))
        else:
            self._set_status("atlas", ToolRunStatus.DISABLED)
        self._finish_run("atlas", started)

        # Step 8: Supply-chain threat scan
        started = time.monotonic()
        if self.enable_supply_chain:
            sc = self._run_supply_chain(sbom_dict, m1_config)
            all_findings.extend(sc)
            if not self._recorded("supply-chain"):
                self._set_status("supply-chain", ToolRunStatus.OK, len(sc))
        else:
            self._set_status("supply-chain", ToolRunStatus.DISABLED)
        self._finish_run("supply-chain", started)

        # Filter by minimum severity
        sev_order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
                     Severity.LOW: 3, Severity.INFO: 4}
        min_rank = sev_order.get(self.min_severity, 4)
        all_findings = [f for f in all_findings if sev_order.get(f.severity, 99) <= min_rank]

        _log.info("static analysis complete: %d finding(s)", len(all_findings))
        return all_findings

    # ------------------------------------------------------------------
    # Finding projection: stable rule identity, kind and locations
    # ------------------------------------------------------------------

    def _index_sbom(self, sbom_json: dict[str, Any]) -> None:
        """Index SBOM nodes by name and deps by purl for location projection."""
        self._nodes_by_name = {}
        for node in sbom_json.get("nodes") or []:
            name = str(node.get("name") or "").strip().lower()
            if name:
                self._nodes_by_name.setdefault(name, []).append(node)
        self._dep_files = {}
        for dep in sbom_json.get("deps") or []:
            src = str(dep.get("source_file") or "")
            if not src:
                continue
            for key in (dep.get("purl"), dep.get("name")):
                if key:
                    self._dep_files.setdefault(str(key).lower(), src)

    def _to_finding(self, raw: dict[str, Any], source: str) -> Finding:
        finding = _raw_to_finding(raw, source)
        try:
            update = self._projection(raw, finding)
        except Exception as exc:  # projection is best-effort metadata
            _log.debug("finding projection failed for %s: %s", finding.rule_id, exc)
            update = {}
        update.update(self._scrub_source_root(finding))
        return finding.model_copy(update=update) if update else finding

    def _scrub_source_root(self, finding: Finding) -> dict[str, Any]:
        """Rewrite absolute paths under source_path in text fields to repo-relative ones.

        Scanners report absolute paths (e.g. Semgrep ``affected`` entries); left
        in place they leak the checkout location and make results differ
        between otherwise identical runs.
        """
        if not self.source_path:
            return {}
        prefixes = _root_prefixes(Path(self.source_path))
        update: dict[str, Any] = {}
        for name in ("title", "description", "affected_component", "evidence", "remediation"):
            value = getattr(finding, name)
            if isinstance(value, str) and any(p in value for p in prefixes):
                update[name] = scrub_paths(value, prefixes)
        return update

    def _projection(self, raw: dict[str, Any], finding: Finding) -> dict[str, Any]:
        tool = finding.source_tool or ""
        update: dict[str, Any] = {}
        locations: list[FindingLocation] = []

        if tool == "semgrep":
            # Semgrep prefixes check_id with the rules file's dotted location
            # (e.g. "nuguard.analysis.plugins.semgrep_rules.<id>"), which differs
            # between a checkout and an installed wheel; keep the bare rule id.
            if finding.rule_id:
                update["rule_id"] = finding.rule_id.rsplit(".", 1)[-1]
            loc = self._location(raw.get("path"), raw.get("scan_target"),
                                 raw.get("start_line"), raw.get("end_line"))
            locations = [loc] if loc else []
        elif tool == "checkov":
            line_range = raw.get("line_range") or []
            loc = self._location(
                raw.get("file_path"), raw.get("scan_target"),
                line_range[0] if line_range else None,
                line_range[-1] if line_range else None,
                symbol=_iac_resource(raw.get("resource")),
            )
            locations = [loc] if loc else []
        elif tool == "trivy":
            if raw.get("source") == "trivy-misconfig":
                update["kind"] = "iac"
            elif raw.get("container_image"):
                update["kind"] = "container"
            if raw.get("container_image"):
                locations = self._paths_to_locations(raw.get("container_image_locations") or [])
            elif raw.get("target"):
                loc = self._location(raw.get("target"), raw.get("scan_target"),
                                     raw.get("start_line"), raw.get("end_line"),
                                     symbol=_iac_resource(raw.get("resource")))
                locations = [loc] if loc else []
        elif tool in ("osv", "grype"):
            if raw.get("container_image"):
                update["kind"] = "container"
                locations = self._paths_to_locations(raw.get("container_image_locations") or [])
            else:
                src = raw.get("source_file") or self._dep_files.get(
                    str(raw.get("purl") or "").lower()
                ) or self._dep_files.get(str(raw.get("dep_name") or "").lower())
                loc = self._location(src, None)
                locations = [loc] if loc else []
            rule = _preferred_advisory_id(raw)
            if rule:
                update["rule_id"] = rule
        elif tool in ("nga-rules", "atlas"):
            node = self._anchor_node(raw)
            if node is not None:
                update["component_ref"] = (
                    f"{node.get('component_type')}:{str(node.get('name') or '').strip().lower()}"
                )
                locations = _node_locations(node)
        elif tool == "supply-chain":
            locations = self._paths_to_locations(
                [a for a in (raw.get("affected") or []) if isinstance(a, str)]
            )

        if locations:
            update["locations"] = locations
        return update

    def _anchor_node(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """SBOM node a posture finding belongs to: explicit anchor, else first affected."""
        anchor = raw.get("anchor") or {}
        candidates: list[tuple[str | None, str]] = []
        if anchor.get("name"):
            candidates.append((anchor.get("type") or None, str(anchor["name"])))
        affected = raw.get("affected") or []
        if isinstance(affected, list):
            candidates.extend((None, str(a)) for a in affected if a)
        for node_type, name in candidates:
            for node in self._nodes_by_name.get(name.strip().lower(), []):
                if node_type is None or node.get("component_type") == node_type:
                    return node
        return None

    def _paths_to_locations(self, paths: list[str]) -> list[FindingLocation]:
        locations: list[FindingLocation] = []
        for entry in paths:
            # Entries may be "path" or "path:line" (Dockerfile evidence, affected lists).
            match = re.match(r"^(.*):(\d+)$", str(entry))
            path, line_no = (match.group(1), int(match.group(2))) if match else (str(entry), None)
            loc = self._location(path, None, line_no, line_no, must_exist=True)
            if loc and loc not in locations:
                locations.append(loc)
        return locations

    def _location(
        self,
        path: Any,
        scan_target: Any,
        start_line: Any = None,
        end_line: Any = None,
        *,
        must_exist: bool = False,
        symbol: str | None = None,
    ) -> FindingLocation | None:
        rel = _relative_path(path, self.source_path, scan_target, must_exist=must_exist)
        if rel is None:
            return None
        start = int(start_line) if isinstance(start_line, int) and start_line > 0 else None
        end = int(end_line) if isinstance(end_line, int) and end_line > 0 else start
        if start is not None and end is not None and end < start:
            end = start
        return FindingLocation(path=rel, start_line=start, end_line=end, symbol=symbol or None)

    # ------------------------------------------------------------------
    # Internal runner helpers
    # ------------------------------------------------------------------

    def _run_nga(
        self,
        sbom_dict: dict[str, Any],
        sbom_graph: "Any | None" = None,
    ) -> list[Finding]:
        """Run NgaRulesPlugin (NGA-001…NGA-030) and annotate with ATLAS techniques."""
        try:
            from nuguard.analysis.plugins.nga_rules import NgaRulesPlugin  # noqa: PLC0415
            from nuguard.common.control_mappings.atlas import NGA_TO_ATLAS  # noqa: PLC0415

            plugin = NgaRulesPlugin()
            # provider="nga-rules" skips the OSV/Grype phases inside the plugin;
            # those are run separately by _run_osv() and _run_grype().
            nga_config: dict[str, Any] = {"provider": "nga-rules", "verbose": self.verbose}
            if sbom_graph is not None:
                nga_config["sbom_graph"] = sbom_graph
            result = plugin.run(sbom_dict, nga_config)

            raw_findings = list(result.findings or [])
            if self.dedupe_posture:
                raw_findings = merge_grouped_findings(raw_findings)

            findings: list[Finding] = []
            for raw in raw_findings:
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
                findings.append(self._to_finding(raw, "nga"))

            self.nga_audit = list(result.details.get("rule_audit") or [])
            rule_errors = list(result.details.get("rule_errors") or [])
            if rule_errors:
                self._set_status(
                    "nga-rules", ToolRunStatus.FAILED, len(findings),
                    reason=f"{len(rule_errors)} rule(s) raised errors: {', '.join(rule_errors[:5])}",
                )
            _log.info("NGA rules: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("NGA rules pass failed: %s", exc)
            self._set_status("nga-rules", ToolRunStatus.FAILED, reason="rule engine error")
            return []

    def _scanner_failure(self, tool: str, exc: BaseException) -> list[Finding]:
        """Record a scanner client failure; keep any findings gathered before it."""
        from nuguard.analysis.scanner_errors import scanner_error_from  # noqa: PLC0415

        err = scanner_error_from(exc)
        partial: list[Finding] = []
        if err.partial_findings:
            from nuguard.analysis.plugins.nga_rules import _grype_to_finding  # noqa: PLC0415

            partial = [self._to_finding(_grype_to_finding(r), tool) for r in err.partial_findings]
        self._set_status(
            tool,
            ToolRunStatus.TIMEOUT if err.timeout else ToolRunStatus.FAILED,
            len(partial),
            reason=err.reason,
        )
        return partial

    def _run_osv(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run OSV querybatch against SBOM deps."""
        try:
            from nuguard.analysis.osv_client import query_osv  # noqa: PLC0415
            from nuguard.analysis.plugins.nga_rules import _osv_to_finding  # noqa: PLC0415

            deps = list(sbom_dict.get("deps") or [])
            if not any(d.get("purl") for d in deps):
                self._set_status(
                    "osv", ToolRunStatus.NOT_APPLICABLE,
                    reason="no package dependencies in SBOM",
                )
                return []
            # _osv_to_finding converts raw OSV dicts to the standard finding shape,
            # including a remediation field with the upgrade instruction.
            raw_list = [_osv_to_finding(r) for r in query_osv(deps, strict=True)]
            findings = [self._to_finding(r, "osv") for r in raw_list]
            _log.info("OSV scan: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("OSV scan failed: %s", exc)
            return self._scanner_failure("osv", exc)

    def _run_grype(self, sbom_dict: dict[str, Any]) -> list[Finding]:
        """Run Grype SBOM + container image scans."""
        try:
            from nuguard.analysis.grype_client import (  # noqa: PLC0415
                query_grype_images,
                query_grype_sbom,
            )
            from nuguard.analysis.plugins.nga_rules import _grype_to_finding  # noqa: PLC0415

            raw_list = [_grype_to_finding(r) for r in query_grype_sbom(
                sbom_dict, timeout=self.grype_timeout, max_retries=self.grype_retries,
                strict=True,
            )]
            container_nodes = [
                n for n in (sbom_dict.get("nodes") or [])
                if (n.get("component_type") or "").upper() == "CONTAINER_IMAGE"
            ]
            if container_nodes:
                raw_list += [_grype_to_finding(r) for r in query_grype_images(
                    container_nodes, timeout=self.grype_timeout, max_retries=self.grype_retries,
                    strict=True,
                )]
            findings = [self._to_finding(r, "grype") for r in raw_list]
            _log.info("Grype scan: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("Grype scan failed: %s", exc)
            return self._scanner_failure("grype", exc)

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
                not_applicable = bool((result.details or {}).get("not_applicable"))
                self._set_status(
                    tool,
                    ToolRunStatus.NOT_APPLICABLE if not_applicable else ToolRunStatus.SKIPPED,
                    reason=result.message or "skipped",
                )
                return []
            raw_list = list(result.findings or [])
            findings = [self._to_finding(r, tool) for r in raw_list]
            if result.status == "error":
                self._set_status(
                    tool,
                    ToolRunStatus.TIMEOUT if _only_timeouts(result.details) else ToolRunStatus.FAILED,
                    len(findings),
                    reason=result.message or "error",
                )
            _log.info("%s: %d finding(s)", tool, len(findings))
            return findings
        except Exception as exc:
            _log.warning("%s scan failed: %s", tool, exc)
            self._set_status(tool, ToolRunStatus.FAILED, reason=f"{tool} plugin error")
            return []

    def _run_supply_chain(
        self, sbom_dict: dict[str, Any], config: dict[str, Any] | None = None
    ) -> list[Finding]:
        """Run SupplyChainPlugin (offline scanner + optional artifact verification)."""
        cfg = config or {}
        try:
            from nuguard.analysis.plugins.supply_chain import SupplyChainPlugin  # noqa: PLC0415

            plugin = SupplyChainPlugin()
            result = plugin.run(sbom_dict, cfg)
            if result.status == "skipped":
                _log.info("supply-chain: skipped (%s)", result.message)
                self._set_status(
                    "supply-chain", ToolRunStatus.SKIPPED, reason=result.message or "skipped"
                )
                return []
            if result.status == "error":
                self._set_status(
                    "supply-chain", ToolRunStatus.FAILED, reason=result.message or "error"
                )
                return []
            raw_list = list(result.findings or [])
            findings = [self._to_finding(r, "supply-chain") for r in raw_list]
            self.sc_audit = list(result.details.get("sc_audit") or [])
            _log.info("supply-chain scan: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("supply-chain scan failed: %s", exc)
            self._set_status("supply-chain", ToolRunStatus.FAILED, reason="supply-chain plugin error")
            return []

    def _run_atlas_native(
        self,
        sbom_dict: dict[str, Any],
        sbom_graph: "Any | None" = None,
    ) -> list[Finding]:
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
            config: dict[str, Any] = {**self.atlas_config, "skip_nga": True}
            if sbom_graph is not None:
                config["sbom_graph"] = sbom_graph
            result = plugin.run(sbom_dict, config)
            raw_list: list[dict[str, Any]] = list(result.details.get("findings") or [])
            findings = [self._to_finding(r, "atlas") for r in raw_list]
            self.token_usage = self.token_usage + result.token_usage
            _log.info("ATLAS native checks: %d finding(s)", len(findings))
            return findings
        except Exception as exc:
            _log.warning("ATLAS native checks failed: %s", exc)
            self._set_status("atlas", ToolRunStatus.FAILED, reason="ATLAS check error")
            return []


def _root_prefixes(root: Path) -> list[str]:
    """Spellings of *root* that may appear in tool output (macOS /private aliases too)."""
    forms = {str(root), str(root.resolve()), os.path.abspath(root)}
    for form in list(forms):
        if form.startswith("/private/"):
            forms.add(form[len("/private"):])
        elif form.startswith(("/var/", "/tmp/")):
            forms.add("/private" + form)
    return sorted({f.rstrip("/") for f in forms if f and f != "/"}, key=len, reverse=True)


def scrub_paths(text: str, prefixes: list[str]) -> str:
    """Replace ``<root>/x`` with ``x`` and a bare ``<root>`` with ``.``."""
    for prefix in prefixes:
        text = text.replace(prefix + "/", "").replace(prefix, ".")
    return text


def _only_timeouts(details: dict[str, Any] | None) -> bool:
    """True when every failed scanner invocation hit a deadline."""
    details = details or {}
    if details.get("errors") and isinstance(details["errors"], list):  # trivy
        return all(e == "timeout" for e in details["errors"])
    failed = [i for i in details.get("invocations") or [] if i.get("status") == "error"]
    return bool(failed) and all(i.get("reason") in ("timeout", "total_timeout") for i in failed)


_HEX_HASH_RE = re.compile(r"^[0-9a-f]{32,64}$")


def _iac_resource(value: Any) -> str | None:
    """IaC resource address, unless the scanner reported a content hash instead."""
    text = str(value or "").strip()
    return text[:300] if text and not _HEX_HASH_RE.match(text) else None


def _preferred_advisory_id(raw: dict[str, Any]) -> str | None:
    """Advisory id for rule_id: keep GHSA/CVE ids, else prefer a CVE alias."""
    adv = str(raw.get("rule_id") or raw.get("advisory_id") or "")
    if adv.upper().startswith(("GHSA-", "CVE-")):
        return adv
    cves = [c for c in raw.get("cve_ids") or [] if str(c).upper().startswith("CVE-")]
    return str(cves[0]) if cves else (adv or None)


def _relative_path(
    path: Any,
    root: Path | None,
    scan_target: Any = None,
    *,
    must_exist: bool = False,
) -> str | None:
    """Repo-relative POSIX path, or None when it cannot be made safely relative.

    Absolute paths are kept only when they resolve inside *root* (symlinks that
    escape the tree are dropped). Scanner paths reported relative to the
    scanned directory (Checkov's "/main.tf", Trivy targets) are joined to
    *scan_target* first.
    """
    if not path or not isinstance(path, str):
        return None
    text = path.strip().replace("\\", "/")
    if not text or "://" in text or text.startswith("pkg:"):
        return None
    root_resolved = Path(root).resolve() if root else None
    candidates: list[Path] = []
    target = Path(str(scan_target)) if scan_target and isinstance(scan_target, str) else None
    if target is not None and target.is_file():
        target = target.parent
    if text.startswith("/") or re.match(r"^[A-Za-z]:/", text):
        candidates.append(Path(text))
        if target is not None:
            candidates.append(target / text.lstrip("/"))
    else:
        if target is not None:
            candidates.append(target / text)
        if root_resolved is not None:
            candidates.append(root_resolved / text)
        elif not must_exist:
            rel = PurePosixPath(text)
            if ".." in rel.parts:
                return None
            return str(rel).removeprefix("./") or None
    if root_resolved is None:
        return None
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if must_exist and not resolved.exists():
            continue
        try:
            rel_path = resolved.relative_to(root_resolved)
        except ValueError:
            continue
        rel_text = rel_path.as_posix()
        if rel_text in ("", "."):
            continue
        return rel_text
    return None


def _node_locations(node: dict[str, Any], limit: int = 10) -> list[FindingLocation]:
    """Evidence locations of an SBOM node (already repo-relative), deduplicated."""
    seen: set[tuple[str, int | None]] = set()
    locations: list[FindingLocation] = []
    for ev in node.get("evidence") or []:
        loc = (ev or {}).get("location") or {}
        raw_path = str(loc.get("path") or "").replace("\\", "/")
        if not raw_path or raw_path.startswith("/") or ".." in PurePosixPath(raw_path).parts:
            continue
        line = loc.get("line") if isinstance(loc.get("line"), int) and loc["line"] > 0 else None
        key = (raw_path, line)
        if key in seen:
            continue
        seen.add(key)
        locations.append(FindingLocation(path=raw_path, start_line=line, end_line=line))
    locations.sort(key=lambda loc: (loc.path, loc.start_line or 0))
    return locations[:limit]


_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def merge_grouped_findings(raws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge raw findings sharing ``rule_id`` + ``dedupe_key`` into one per group.

    Rules that fire once per model-name string attach ``dedupe_key`` /
    ``dedupe_member`` / ``group_title`` (see ``nga_rules._group_meta``). The
    merged finding keeps a stable title (no member count), lists every member
    in ``evidence``, and unions ``affected``. Findings without a key pass
    through unchanged, and order is preserved by first occurrence.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str] | int] = []
    for index, raw in enumerate(raws):
        key = raw.get("dedupe_key")
        if not key:
            order.append(index)
            continue
        group_key = (str(raw.get("rule_id") or ""), str(key))
        if group_key not in groups:
            groups[group_key] = []
            order.append(group_key)
        groups[group_key].append(raw)

    merged: list[dict[str, Any]] = []
    for item in order:
        if isinstance(item, int):
            merged.append(raws[item])
            continue
        members = groups[item]
        if len(members) == 1:
            merged.append(members[0])
            continue
        first = dict(members[0])
        names = sorted({str(m.get("dedupe_member") or "") for m in members} - {""})
        affected: list[str] = []
        for m in members:
            for a in m.get("affected") or []:
                if a not in affected:
                    affected.append(a)
        first["severity"] = min(
            (str(m.get("severity") or "LOW").upper() for m in members),
            key=lambda sev: _SEVERITY_RANK.get(sev, 9),
        )
        first["title"] = first.get("group_title") or first.get("title")
        first["remediation"] = first.get("group_remediation") or first.get("remediation")
        first["affected"] = affected
        first["evidence"] = f"{len(names)} model(s): {', '.join(names)}"
        first["description"] = (
            f"{first.get('group_title') or first.get('title')}. "
            f"Applies to {len(names)} model(s): {', '.join(names)}."
        )
        merged.append(first)
    return merged


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
