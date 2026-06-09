"""``nuguard analyze`` — static risk analysis from an AI-SBOM.

Exit codes
----------
0  No findings at or above ``--min-severity``
1  One or more findings at or above ``--min-severity``
2  Analysis error (SBOM could not be read / parsed)
3  Not implemented / reserved
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import typer

from nuguard.models.finding import Finding, Severity

analyze_app = typer.Typer(
    help="Static risk analysis from the AI-SBOM (no running app required).",
    no_args_is_help=True,
)

_log = logging.getLogger("cli.analyze")

_SEV_ORDER: dict[str, int] = {
    "critical": 0,
    "high":     1,
    "medium":   2,
    "low":      3,
    "info":     4,
}

_SEV_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🟢",
    "info":     "ℹ️",
}


@analyze_app.callback(invoke_without_command=True)
def analyze(
    ctx: typer.Context,
    sbom: Optional[str] = typer.Option(None, "--sbom", help="Path to AI-SBOM JSON file. Falls back to 'sbom:' in nuguard.yaml when --config is set."),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="Path to nuguard.yaml config file. CLI flags override config values.",
    ),
    nga: bool = typer.Option(
        False, "--nga",
        help="Run NGA structural rules only (NGA-001–018); skip OSV, Grype, Checkov, Trivy, Semgrep, and ATLAS native checks.",
    ),
    format: str = typer.Option(
        "markdown", "--format", "-f",
        help="Output format: markdown | sarif | json.",
    ),
    policy: str = typer.Option(
        None, "--policy",
        help="Path to Cognitive Policy Markdown file (policy check not yet implemented).",
    ),
    min_severity: Optional[str] = typer.Option(
        None, "--min-severity",
        help="Minimum severity to report: critical | high | medium | low | info. [default: medium]",
    ),
    atlas: bool = typer.Option(True, "--atlas/--no-atlas", help="Run MITRE ATLAS native graph checks."),  # noqa: E501
    osv: bool = typer.Option(True, "--osv/--no-osv", help="Run OSV dependency CVE scan."),
    grype: bool = typer.Option(True, "--grype/--no-grype",
                               help="Run Grype CVE scan (requires grype on PATH)."),
    grype_timeout: Optional[float] = typer.Option(
        None, "--grype-timeout",
        help="Per-invocation timeout for grype in seconds. [default: 180]",
    ),
    grype_retries: Optional[int] = typer.Option(
        None, "--grype-retries",
        help="Number of retry attempts when grype times out. [default: 3]",
    ),
    checkov: bool = typer.Option(True, "--checkov/--no-checkov",
                                 help="Run Checkov IaC scan (requires checkov on PATH)."),
    trivy: bool = typer.Option(True, "--trivy/--no-trivy",
                               help="Run Trivy container/fs scan (requires trivy on PATH)."),
    semgrep: bool = typer.Option(True, "--semgrep/--no-semgrep",
                                 help="Run Semgrep AI-security rules (requires semgrep on PATH)."),
    source: Optional[str] = typer.Option(
        None, "--source", "-s",
        help="Path to app source directory for supply-chain, Checkov, Trivy, and Semgrep scans. Falls back to 'source:' in nuguard.yaml.",
    ),
    supply_chain: bool = typer.Option(True, "--supply-chain/--no-supply-chain",
                                      help="Run supply-chain threat pack (NGA-SC-001–025)."),
    supply_chain_profile: Optional[str] = typer.Option(
        None, "--supply-chain-profile",
        help="Supply-chain scan profile: ci | standard | full. [default: standard]",
    ),
    supply_chain_verify: Optional[str] = typer.Option(
        None, "--supply-chain-verify",
        help="Artifact registry verification: off | warn | fail. [default: off]",
    ),
    llm: bool = typer.Option(False, "--llm", help="Enable LLM enrichment in ATLAS pass."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show all 18 NGA rules (pass and fail) with evidence on why each passed.",
    ),
    output: str = typer.Option(
        None, "--output", "-o",
        help="Write report to this file instead of stdout.",
    ),
) -> None:
    """Run static analysis against the AI-SBOM.

    Scans the SBOM for structural security issues using NGA rules (NGA-001 to
    NGA-018), checks dependencies against the OSV CVE database, optionally runs
    Grype for container/package CVEs, and annotates findings with MITRE ATLAS v2
    technique mappings.

    Use ``--config nuguard.yaml`` to load ``analyze.min_severity`` and
    ``analyze.nga_only`` from the project config file. CLI flags always
    override config values.

    Use ``--nga`` to run only NGA structural rules (fastest mode, no external
    tools required). Equivalent to setting ``analyze.nga_only: true`` in
    nuguard.yaml.
    """
    if ctx.invoked_subcommand is not None:
        return

    # ------------------------------------------------------------------
    # Load config and resolve effective flag values
    # ------------------------------------------------------------------
    from nuguard.config import load_config  # noqa: PLC0415
    cfg = load_config(config)

    # --nga: CLI flag wins; fall back to config field
    nga = nga or cfg.analyze_nga_only

    # --min-severity: CLI wins when explicitly set (non-None); else use config default
    min_severity = min_severity or cfg.analyze_min_severity

    # --source: CLI wins; fall back to top-level source: in nuguard.yaml
    source = source or cfg.source_path

    # supply-chain: CLI wins; fall back to analyze: section in nuguard.yaml
    sc_profile = supply_chain_profile or cfg.analyze_supply_chain_profile
    sc_verify = supply_chain_verify or cfg.analyze_supply_chain_verify

    # NGA-only mode: disable all external scans
    if nga:
        osv = grype = checkov = trivy = semgrep = atlas = False

    # ------------------------------------------------------------------
    # Load SBOM
    # ------------------------------------------------------------------
    sbom = sbom or cfg.sbom_path
    if not sbom:
        typer.echo("error: --sbom is required (or set 'sbom:' in nuguard.yaml via --config)", err=True)
        raise typer.Exit(code=2)

    sbom_path = Path(sbom)
    if not sbom_path.exists():
        typer.echo(f"error: SBOM file not found: {sbom_path}", err=True)
        raise typer.Exit(code=2)

    try:
        sbom_data = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        typer.echo(f"error: failed to read SBOM: {exc}", err=True)
        raise typer.Exit(code=2)

    try:
        from nuguard.sbom.models import AiSbomDocument  # noqa: PLC0415
        doc = AiSbomDocument.model_validate(sbom_data)
    except Exception as exc:
        typer.echo(f"error: SBOM validation failed: {exc}", err=True)
        raise typer.Exit(code=2)

    # ------------------------------------------------------------------
    # Run analysis
    # ------------------------------------------------------------------
    min_sev_str = min_severity.lower()
    if min_sev_str not in _SEV_ORDER:
        typer.echo(f"error: unknown --min-severity '{min_severity}'", err=True)
        raise typer.Exit(code=2)
    min_sev = Severity(min_sev_str) if min_sev_str != "info" else Severity.INFO

    atlas_config: dict[str, Any] = {}
    if llm:
        atlas_config["llm"] = True
    if format == "markdown":
        atlas_config["format"] = "markdown"

    try:
        from nuguard.analysis.static_analyzer import StaticAnalyzer  # noqa: PLC0415
        source_path = Path(source) if source else None
        analyzer = StaticAnalyzer(
            enable_atlas=atlas,
            enable_osv=osv,
            enable_grype=grype,
            enable_checkov=checkov,
            enable_trivy=trivy,
            enable_semgrep=semgrep,
            enable_supply_chain=supply_chain,
            supply_chain_profile=sc_profile,
            supply_chain_verify_artifacts=sc_verify,
            source_path=source_path,
            atlas_config=atlas_config,
            min_severity=min_sev,
            verbose=verbose,
            grype_timeout=grype_timeout if grype_timeout is not None else 180.0,
            grype_retries=grype_retries if grype_retries is not None else 3,
        )
        findings = analyzer.analyze(doc)
    except Exception as exc:
        typer.echo(f"error: analysis failed: {exc}", err=True)
        _log.exception("analysis failed")
        raise typer.Exit(code=2)

    # ------------------------------------------------------------------
    # Filter to requested minimum severity
    # ------------------------------------------------------------------
    min_rank = _SEV_ORDER.get(min_sev_str, 4)
    visible = [
        f for f in findings
        if _SEV_ORDER.get(f.severity.value, 99) <= min_rank
    ]

    # ------------------------------------------------------------------
    # Render output
    # ------------------------------------------------------------------
    fmt = format.lower()
    tool_status = getattr(analyzer, "tool_status", {})
    nga_audit = getattr(analyzer, "nga_audit", [])
    sc_audit = getattr(analyzer, "sc_audit", [])
    if fmt == "json":
        report_text = _render_json(visible, sbom_path, tool_status, nga_audit, sc_audit)
    elif fmt == "sarif":
        report_text = _render_sarif(visible, sbom_path, tool_status)
    else:
        report_text = _render_markdown(visible, sbom_path, min_severity, tool_status, nga_audit, sc_audit)

    if output:
        out_path = Path(output)
        out_path.write_text(report_text, encoding="utf-8")
        typer.echo(f"report written to {out_path}")
    else:
        typer.echo(report_text)

    # Exit 1 if any findings at or above threshold
    raise typer.Exit(code=1 if visible else 0)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _component_label(f: Finding) -> str:
    """Return a normalised display label for a finding's component.

    PURLs (pkg:npm/next@15.2.4) are simplified to ``name@version`` so that
    findings from different scanners for the same package are grouped together.
    """
    raw = f.affected_component or f.finding_id.rsplit("-", 1)[0]
    # Normalise PURL: pkg:npm/next@15.2.4 → next@15.2.4
    if raw.startswith("pkg:"):
        # strip scheme+type prefix  e.g. "pkg:npm/" or "pkg:pypi/"
        raw = raw.split("/", 1)[-1]
    return raw


def _source_tool(f: Finding) -> str:
    """Extract the originating scanner name from the finding_id prefix.

    finding_id format: ``<tool>-<rest>``  e.g. ``trivy-CVE-2025-...``,
    ``osv-GHSA-...``, ``nga-NGA-001-...``.
    """
    prefix = f.finding_id.split("-")[0].lower()
    # known prefixes
    if prefix in ("trivy", "osv", "grype", "nga", "checkov", "semgrep", "atlas"):
        return prefix
    return prefix


_CVE_RE = re.compile(r'\bCVE-\d{4}-\d+\b', re.IGNORECASE)
_GHSA_RE = re.compile(r'\bGHSA-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+\b', re.IGNORECASE)


def _canonical_vuln_id(f: Finding) -> str | None:
    """Extract a canonical CVE or GHSA identifier for cross-tool deduplication.

    Returns the CVE-XXXX-XXXXX id when available (preferred), then GHSA-..., then
    None for structural findings (NGA, ATLAS, semgrep, checkov) that are not
    package vulnerabilities and should never be merged.
    """
    if _source_tool(f) in ("nga", "atlas", "semgrep", "checkov"):
        return None
    # CVE embedded in finding_id (trivy, grype)
    m = _CVE_RE.search(f.finding_id)
    if m:
        return m.group().upper()
    # CVE aliased in title (OSV sometimes adds "[CVE-XXXX-XXXXX]")
    m = _CVE_RE.search(f.title or "")
    if m:
        return m.group().upper()
    # CVE mentioned in description
    m = _CVE_RE.search(f.description or "")
    if m:
        return m.group().upper()
    # GHSA in finding_id (OSV primary identifier)
    m = _GHSA_RE.search(f.finding_id)
    if m:
        return m.group().upper()
    return None


def _dedup_component_findings(
    flist: list[Finding],
) -> list[tuple[Finding, list[str]]]:
    """Deduplicate findings within a component by CVE/GHSA identity.

    Returns a list of (canonical_finding, [source_tool, ...]) tuples.
    When multiple tools report the same CVE:
      - OSV is preferred as canonical (for its osv.dev link and remediation text)
      - All originating tool names are collected for display
    Non-CVE findings (NGA structural rules, ATLAS, …) pass through unchanged.
    """
    groups: dict[str, list[Finding]] = {}
    no_key: list[Finding] = []
    for f in flist:
        key = _canonical_vuln_id(f)
        if key is None:
            no_key.append(f)
        else:
            groups.setdefault(key, []).append(f)

    result: list[tuple[Finding, list[str]]] = []
    for _key, group in groups.items():
        sources = sorted({_source_tool(f) for f in group})
        # Prefer OSV as canonical — it carries osv.dev links and remediation guidance
        osv_findings = [f for f in group if _source_tool(f) == "osv"]
        canonical = osv_findings[0] if osv_findings else group[0]
        result.append((canonical, sources))

    for f in no_key:
        result.append((f, [_source_tool(f)]))

    result.sort(key=lambda x: _SEV_ORDER.get(x[0].severity.value, 99))
    return result


def _group_by_component(findings: list[Finding]) -> list[tuple[str, list[Finding]]]:
    """Group findings by component.

    Components are ordered by highest severity first (critical → high → …),
    then by finding count (desc), then alphabetically.
    Within each component findings are sorted by severity (critical first).
    """
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        key = _component_label(f)
        grouped.setdefault(key, []).append(f)

    # Sort each component's findings by severity
    for flist in grouped.values():
        flist.sort(key=lambda x: _SEV_ORDER.get(x.severity.value, 99))

    # Sort components: highest severity first, then most findings, then alphabetically
    return sorted(
        grouped.items(),
        key=lambda kv: (_SEV_ORDER.get(kv[1][0].severity.value, 99), -len(kv[1]), kv[0]),
    )


def _render_rule_audit_section(
    audit: list[dict[str, Any]],
    section_title: str,
    description: str,
) -> list[str]:
    """Render a pass/fail rule audit table + per-passing-rule detail block."""
    _AUDIT_SEV_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
    _AUDIT_STATUS_ICON = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️", "SKIPPED": "⏭️"}
    lines: list[str] = [
        f"## {section_title}", "",
        description,
        "",
        "| Rule | Severity | Status | Evidence |",
        "|------|----------|--------|----------|",
    ]
    for entry in audit:
        rid = entry.get("rule_id", "")
        title = entry.get("title", "")
        sev = entry.get("severity", "")
        status = entry.get("status", "")
        sev_em = _AUDIT_SEV_EMOJI.get(sev, "")
        st_icon = _AUDIT_STATUS_ICON.get(status, "❓")
        if status == "FAIL":
            count = entry.get("finding_count", 0)
            affected = entry.get("affected", [])
            detail = f"{count} finding(s)"
            if affected:
                detail += f" — `{'`, `'.join(str(a) for a in affected[:3])}`"
                if len(affected) > 3:
                    detail += f" +{len(affected) - 3} more"
        elif status == "SKIPPED":
            detail = entry.get("pass_reason", "not in profile")
        else:
            detail = entry.get("pass_reason", "")
        lines.append(
            f"| **{rid}** {title} | {sev_em} {sev} | {st_icon} {status} | {detail} |"
        )
    lines.append("")
    # Per-rule detail for passing rules
    pass_rules = [e for e in audit if e.get("status") == "PASS"]
    if pass_rules:
        lines += ["### Passing Rule Details", ""]
        for entry in pass_rules:
            rid = entry.get("rule_id", "")
            title = entry.get("title", "")
            checks = entry.get("checks", "")
            pass_reason = entry.get("pass_reason", "")
            lines += [
                f"**{rid} — {title}**  ",
                f"- Examined: {checks}  ",
                f"- Result: {pass_reason}  ",
            ]
            evidence: dict[str, Any] = entry.get("pass_evidence") or {}
            if evidence:
                lines.append("- Evidence:  ")
                for key, val in evidence.items():
                    label = key.replace("_", " ")
                    if isinstance(val, list):
                        val_str = ", ".join(f"`{x}`" for x in val) if val else "none"
                    elif isinstance(val, bool):
                        val_str = str(val).lower()
                    else:
                        val_str = str(val)
                    lines.append(f"  - {label}: {val_str}  ")
            lines.append("")
    return lines


def _render_markdown(
    findings: list[Finding],
    sbom_path: Path,
    min_severity: str,
    tool_status: dict[str, Any] | None = None,
    nga_audit: list[dict[str, Any]] | None = None,
    sc_audit: list[dict[str, Any]] | None = None,
) -> str:
    # Pre-compute deduplicated view used throughout the report.
    # Dedup is per-component (same CVE from trivy+osv → one entry, OSV canonical).
    grouped_raw = _group_by_component(findings)
    grouped_deduped = [
        (comp, _dedup_component_findings(flist))
        for comp, flist in grouped_raw
    ]
    # Flat list of canonical findings (one per unique CVE per component)
    deduped_all: list[Finding] = [f for _, entries in grouped_deduped for f, _ in entries]

    lines: list[str] = [
        "# NuGuard Static Analysis Report",
        "",
        f"**SBOM:** `{sbom_path}`  ",
        f"**Minimum severity:** {min_severity}  ",
        f"**Total findings:** {len(deduped_all)} unique",
        f"*(from {len(findings)} raw tool findings — duplicates across scanners merged)*  ",
        "",
    ]

    # ------------------------------------------------------------------
    # Severity summary (based on deduplicated findings)
    # ------------------------------------------------------------------
    if deduped_all:
        by_sev: dict[str, list[Finding]] = {}
        for f in deduped_all:
            by_sev.setdefault(f.severity.value, []).append(f)

        summary_parts: list[str] = []
        for sev in ("critical", "high", "medium", "low", "info"):
            grp = by_sev.get(sev, [])
            if grp:
                emoji = _SEV_EMOJI.get(sev, "")
                comps = len({_component_label(f) for f in grp})
                summary_parts.append(
                    f"{emoji} **{sev.upper()}:** {len(grp)} finding(s) across {comps} component(s)"
                )
        lines += ["## Summary", ""] + [f"- {p}" for p in summary_parts] + [""]

        # Top components by unique finding count
        top_n = grouped_deduped[:5]
        lines += ["### Components with Most Findings", ""]
        lines += ["| Component | Unique CVEs | Highest Severity |",
                  "|-----------|-------------|-----------------|"]
        for comp, entries in top_n:
            top_sev = entries[0][0].severity.value  # sorted by severity
            emoji = _SEV_EMOJI.get(top_sev, "")
            lines.append(f"| `{comp}` | {len(entries)} | {emoji} {top_sev.upper()} |")
        lines.append("")

    # ------------------------------------------------------------------
    # Tool coverage table
    # ------------------------------------------------------------------
    if tool_status:
        _STATUS_ICON = {"ok": "✅", "skipped": "⏭️", "disabled": "🔕", "error": "❌"}
        lines += [
            "## Tool Coverage", "",
            "| Tool | Status | Findings |",
            "|------|--------|----------|",
        ]
        for tool, info in tool_status.items():
            st = info.get("status", "?")
            icon = _STATUS_ICON.get(st, "❓")
            count = info.get("findings", "—")
            reason = info.get("reason", "")
            note = f" ({reason})" if reason and st in ("skipped", "error") else ""
            lines.append(f"| {tool} | {icon} {st}{note} | {count} |")
        lines.append("")

    if not deduped_all:
        lines += ["_No findings at or above the requested severity threshold._", ""]
    else:
        # ------------------------------------------------------------------
        # Findings grouped by component — one entry per unique CVE/GHSA.
        # Same vulnerability reported by multiple scanners is shown once;
        # OSV's finding is canonical (osv.dev link + remediation).
        # ------------------------------------------------------------------
        lines += ["## Findings", ""]

        for comp, entries in grouped_deduped:
            top_sev = entries[0][0].severity.value
            emoji = _SEV_EMOJI.get(top_sev, "")
            lines += [f"### {emoji} `{comp}` ({len(entries)} unique CVE(s))", ""]
            for f, sources in entries:
                sev_emoji = _SEV_EMOJI.get(f.severity.value, "")
                vuln_id = _canonical_vuln_id(f) or f.finding_id
                sources_str = ", ".join(f"`{s}`" for s in sources)
                lines += [f"#### {sev_emoji} {vuln_id}  {f.title}", ""]
                lines += [f"**Sources:** {sources_str}  ", ""]
                lines += [f.description or "", ""]
                if f.affected_component:
                    lines += [f"**Affected:** `{_component_label(f)}`  ", ""]
                if f.remediation:
                    lines += [f"**Remediation:** {f.remediation}  ", ""]
                if f.mitre_atlas_technique:
                    lines += [f"**ATLAS Techniques:** {f.mitre_atlas_technique}  ", ""]
                if f.references:
                    lines += ["**References:**  ", ""]
                    for ref in f.references:
                        lines.append(f"- {ref}")
                    lines.append("")

    # ------------------------------------------------------------------
    # NGA Rule Audit (verbose mode only)
    # ------------------------------------------------------------------
    if nga_audit:
        lines += _render_rule_audit_section(
            nga_audit,
            "NGA Rule Audit",
            "All 18 NGA structural rules — pass/fail status with evidence.",
        )

    # ------------------------------------------------------------------
    # Supply Chain Rule Audit
    # ------------------------------------------------------------------
    if sc_audit:
        lines += _render_rule_audit_section(
            sc_audit,
            "Supply Chain Rule Audit",
            "All 25 NGA-SC supply-chain rules — pass/fail/skipped status with evidence.",
        )

    return "\n".join(lines)


def _render_json(
    findings: list[Finding],
    sbom_path: Path,
    tool_status: dict[str, Any] | None = None,
    nga_audit: list[dict[str, Any]] | None = None,
    sc_audit: list[dict[str, Any]] | None = None,
) -> str:
    # Severity counts
    sev_counts: dict[str, int] = {}
    for f in findings:
        sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1

    # Findings grouped by component, sorted by severity within each group
    by_component: dict[str, list[dict[str, Any]]] = {}
    for comp, flist in _group_by_component(findings):
        by_component[comp] = [f.model_dump() for f in flist]

    data: dict[str, Any] = {
        "sbom": str(sbom_path),
        "total": len(findings),
        "severity_counts": sev_counts,
        "tool_status": tool_status or {},
        "by_component": by_component,
        "findings": [f.model_dump() for f in findings],
        **({"nga_rule_audit": nga_audit} if nga_audit else {}),
        **({"sc_rule_audit": sc_audit} if sc_audit else {}),
    }
    return json.dumps(data, indent=2, default=str)


def _render_sarif(
    findings: list[Finding],
    sbom_path: Path,
    tool_status: dict[str, Any] | None = None,
) -> str:
    """SARIF 2.1.0 output with tool coverage in the run's properties."""
    _sev_to_sarif = {
        "critical": "error",
        "high":     "error",
        "medium":   "warning",
        "low":      "note",
        "info":     "none",
    }
    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    seen_rules: set[str] = set()

    for f in findings:
        rule_id = f.finding_id.rsplit("-", 1)[0]  # strip uuid suffix
        if rule_id not in seen_rules:
            seen_rules.add(rule_id)
            rules.append({
                "id": rule_id,
                "name": f.title,
                "shortDescription": {"text": f.description or f.title},
                "helpUri": f.references[0] if f.references else "",
            })
        results.append({
            "ruleId": rule_id,
            "level": _sev_to_sarif.get(f.severity.value, "warning"),
            "message": {"text": f.description or f.title},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": str(sbom_path)},
                    }
                }
            ],
        })

    sarif: dict[str, Any] = {
        "version": "2.1.0",
        "$schema": "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "nuguard",
                        "informationUri": "https://github.com/anthropics/nuguard",
                        "rules": rules,
                    }
                },
                "properties": {
                    "toolCoverage": tool_status or {},
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)
