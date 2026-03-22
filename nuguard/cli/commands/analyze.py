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
from pathlib import Path
from typing import Any

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
    sbom: str = typer.Option(..., "--sbom", help="Path to AI-SBOM JSON file."),
    format: str = typer.Option(
        "markdown", "--format", "-f",
        help="Output format: markdown | sarif | json.",
    ),
    policy: str = typer.Option(
        None, "--policy",
        help="Path to Cognitive Policy Markdown file (policy check not yet implemented).",
    ),
    min_severity: str = typer.Option(
        "medium", "--min-severity",
        help="Minimum severity to report: critical | high | medium | low | info.",
    ),
    atlas: bool = typer.Option(True, "--atlas/--no-atlas", help="Run MITRE ATLAS native graph checks."),  # noqa: E501
    osv: bool = typer.Option(True, "--osv/--no-osv", help="Run OSV dependency CVE scan."),
    grype: bool = typer.Option(True, "--grype/--no-grype",
                               help="Run Grype CVE scan (requires grype on PATH)."),
    checkov: bool = typer.Option(True, "--checkov/--no-checkov",
                                 help="Run Checkov IaC scan (requires checkov on PATH)."),
    trivy: bool = typer.Option(True, "--trivy/--no-trivy",
                               help="Run Trivy container/fs scan (requires trivy on PATH)."),
    semgrep: bool = typer.Option(True, "--semgrep/--no-semgrep",
                                 help="Run Semgrep AI-security rules (requires semgrep on PATH)."),
    source: str = typer.Option(
        None, "--source", "-s",
        help="Path to app source directory for Checkov/Trivy/Semgrep scans.",
    ),
    llm: bool = typer.Option(False, "--llm", help="Enable LLM enrichment in ATLAS pass."),
    output: str = typer.Option(
        None, "--output", "-o",
        help="Write report to this file instead of stdout.",
    ),
) -> None:
    """Run static analysis against the AI-SBOM.

    Scans the SBOM for structural security issues using NGA rules (NGA-001 to
    NGA-019), checks dependencies against the OSV CVE database, optionally runs
    Grype for container/package CVEs, and annotates findings with MITRE ATLAS v2
    technique mappings.
    """
    if ctx.invoked_subcommand is not None:
        return

    # ------------------------------------------------------------------
    # Load SBOM
    # ------------------------------------------------------------------
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
        from nuguard.models.sbom import AiSbomDocument  # noqa: PLC0415
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
            source_path=source_path,
            atlas_config=atlas_config,
            min_severity=min_sev,
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
    if fmt == "json":
        report_text = _render_json(visible, sbom_path, tool_status)
    elif fmt == "sarif":
        report_text = _render_sarif(visible, sbom_path, tool_status)
    else:
        report_text = _render_markdown(visible, sbom_path, min_severity, tool_status)

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
    """Return the display label for a finding's component."""
    return f.affected_component or f.finding_id.rsplit("-", 1)[0]


def _group_by_component(findings: list[Finding]) -> list[tuple[str, list[Finding]]]:
    """Group findings by component, sorted by finding count (desc), then name.

    Within each component group findings are sorted by severity (critical first).
    """
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        key = _component_label(f)
        grouped.setdefault(key, []).append(f)

    # Sort each component's findings by severity
    for flist in grouped.values():
        flist.sort(key=lambda x: _SEV_ORDER.get(x.severity.value, 99))

    # Sort components: most findings first, then alphabetically
    return sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _render_markdown(
    findings: list[Finding],
    sbom_path: Path,
    min_severity: str,
    tool_status: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = [
        "# NuGuard Static Analysis Report",
        "",
        f"**SBOM:** `{sbom_path}`  ",
        f"**Minimum severity:** {min_severity}  ",
        f"**Total findings:** {len(findings)}  ",
        "",
    ]

    # ------------------------------------------------------------------
    # Severity summary
    # ------------------------------------------------------------------
    if findings:
        by_sev: dict[str, list[Finding]] = {}
        for f in findings:
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

        # Top components by finding count
        grouped = _group_by_component(findings)
        top_n = grouped[:5]
        lines += ["### Components with Most Findings", ""]
        lines += ["| Component | Findings | Highest Severity |",
                  "|-----------|----------|-----------------|"]
        for comp, flist in top_n:
            top_sev = flist[0].severity.value  # already sorted by severity
            emoji = _SEV_EMOJI.get(top_sev, "")
            lines.append(f"| `{comp}` | {len(flist)} | {emoji} {top_sev.upper()} |")
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

    if not findings:
        lines += ["_No findings at or above the requested severity threshold._", ""]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Findings grouped by component, sorted by severity within each group
    # ------------------------------------------------------------------
    lines += ["## Findings", ""]

    for comp, flist in _group_by_component(findings):
        top_sev = flist[0].severity.value
        emoji = _SEV_EMOJI.get(top_sev, "")
        lines += [f"### {emoji} `{comp}` ({len(flist)} finding(s))", ""]
        for f in flist:
            sev_emoji = _SEV_EMOJI.get(f.severity.value, "")
            lines += [f"#### {sev_emoji} {f.finding_id}  {f.title}", ""]
            lines += [f.description or "", ""]
            if f.affected_component:
                lines += [f"**Affected:** `{f.affected_component}`  ", ""]
            if f.remediation:
                lines += [f"**Remediation:** {f.remediation}  ", ""]
            if f.mitre_atlas_technique:
                lines += [f"**ATLAS Techniques:** {f.mitre_atlas_technique}  ", ""]
            if f.references:
                lines += ["**References:**  ", ""]
                for ref in f.references:
                    lines.append(f"- {ref}")
                lines.append("")

    return "\n".join(lines)


def _render_json(
    findings: list[Finding],
    sbom_path: Path,
    tool_status: dict[str, Any] | None = None,
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
