"""``nuguard scan`` — unified security scan meta-command.

Runs the configurable nuguard security scan pipeline in a single command:

  Step 1  sbom      Generate AI-SBOM from source (``nuguard sbom generate``)
  Step 2  analyze   Run all detectors (NGA rules + OSV + Grype + Checkov +
                    Trivy + Semgrep + ATLAS annotation)
  Step 3  policy    Check Cognitive Policy document (if requested and --policy supplied)
  Step 4  redteam   Dynamic red-team (if requested and a target URL is available)

Exit codes
----------
0  Clean — no findings at or above ``--fail-on`` threshold
1  Findings at or above threshold
2  Critical findings (always non-zero regardless of ``--fail-on``)
3  Scan error (SBOM generation failure, analysis crash, etc.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity

scan_app = typer.Typer(
    help="Unified security scan: SBOM → analyze, with optional policy/redteam.",
    no_args_is_help=True,
)

_log = get_logger("cli.scan")

_SEV_ORDER: dict[str, int] = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4
}


@scan_app.callback(invoke_without_command=True)
def scan(
    ctx: typer.Context,
    source: str = typer.Option(
        ".", "--source", "-s",
        help="Path to AI application source directory.",
    ),
    output_dir: str = typer.Option(
        "nuguard-reports", "--output-dir", "-o",
        help="Directory for all output files.",
    ),
    fail_on: str = typer.Option(
        "high", "--fail-on",
        help="Minimum severity that triggers exit code 1: critical | high | medium | low.",
    ),
    steps: str = typer.Option(
        "sbom,analyze", "--steps",
        help="Comma-separated subset of steps: sbom,analyze,policy,redteam.",
    ),
    policy: str = typer.Option(
        None, "--policy",
        help="Path to Cognitive Policy Markdown file.",
    ),
    target: str = typer.Option(
        None, "--target",
        help="Live app URL for the redteam step; falls back to SBOM deployment URL when available.",
    ),
    container_image: str = typer.Option(
        None, "--container-image",
        help="Container image ref for Trivy image scan (e.g. myapp:latest).",
    ),
    llm: bool = typer.Option(
        False, "--llm",
        help="Enable LLM enrichment in the ATLAS annotation pass.",
    ),
    no_atlas: bool = typer.Option(
        False, "--no-atlas",
        help="Skip MITRE ATLAS annotation pass.",
    ),
    no_osv: bool = typer.Option(
        False, "--no-osv",
        help="Skip OSV dependency CVE scan.",
    ),
    no_grype: bool = typer.Option(
        False, "--no-grype",
        help="Skip Grype CVE scan.",
    ),
    no_checkov: bool = typer.Option(
        False, "--no-checkov",
        help="Skip Checkov IaC scan.",
    ),
    no_trivy: bool = typer.Option(
        False, "--no-trivy",
        help="Skip Trivy container/fs scan.",
    ),
    no_semgrep: bool = typer.Option(
        False, "--no-semgrep",
        help="Skip Semgrep AI-security rules scan.",
    ),
    no_supply_chain: bool = typer.Option(
        False, "--no-supply-chain",
        help="Skip supply-chain threat scan (lifecycle scripts, AI-agent configs, workflows).",
    ),
    supply_chain_profile: str = typer.Option(
        "standard", "--supply-chain-profile",
        help="Supply-chain scan profile: ci | standard | full.",
    ),
    supply_chain_verify: str = typer.Option(
        "off", "--supply-chain-verify",
        help="Registry artifact verification: off | warn | fail (default: off).",
    ),
) -> None:
    """Run the configurable nuguard security scan pipeline.

    Generates an AI-SBOM, runs all enabled detectors, writes terminal output,
    ``report.md``, ``findings.sarif``, and ``findings.json`` to the output
    directory, then exits with the appropriate code.
    """
    if ctx.invoked_subcommand is not None:
        return

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_path = Path(source).resolve()

    enabled_steps = {s.strip().lower() for s in steps.split(",")}

    fail_sev = fail_on.lower()
    if fail_sev not in _SEV_ORDER:
        typer.echo(f"error: unknown --fail-on value '{fail_on}'", err=True)
        raise typer.Exit(code=3)

    # ------------------------------------------------------------------
    # Step 1: SBOM generation
    # ------------------------------------------------------------------
    sbom_path = out_dir / "sbom.json"
    sbom_dict: dict[str, Any] = {}

    if "sbom" in enabled_steps:
        typer.echo(f"[1/4] Generating AI-SBOM for {src_path} …")
        try:
            from nuguard.sbom.extractor.config import AiSbomConfig  # noqa: PLC0415
            from nuguard.sbom.extractor.core import AiSbomExtractor  # noqa: PLC0415
            from nuguard.sbom.serializer import AiSbomSerializer  # noqa: PLC0415

            cfg = AiSbomConfig()
            extractor = AiSbomExtractor()
            doc = extractor.extract_from_path(str(src_path), cfg)
            sbom_dict = json.loads(AiSbomSerializer.to_json(doc))
            sbom_path.write_text(json.dumps(sbom_dict, indent=2), encoding="utf-8")
            typer.echo(f"    ✓ SBOM written → {sbom_path}")
        except Exception as exc:
            typer.echo(f"error: SBOM generation failed: {exc}", err=True)
            _log.exception("SBOM generation failed")
            raise typer.Exit(code=3)
    elif sbom_path.exists():
        try:
            sbom_dict = json.loads(sbom_path.read_text(encoding="utf-8"))
            typer.echo(f"[1/4] Using existing SBOM → {sbom_path}")
        except Exception as exc:
            typer.echo(f"error: cannot read SBOM at {sbom_path}: {exc}", err=True)
            raise typer.Exit(code=3)
    else:
        typer.echo(f"warning: 'sbom' step skipped and no existing sbom at {sbom_path}", err=True)

    # Parse SBOM document (needed for policy and redteam steps even when analyze is skipped)
    sbom_doc = None
    if sbom_dict:
        try:
            from nuguard.sbom.models import AiSbomDocument  # noqa: PLC0415
            sbom_doc = AiSbomDocument.model_validate(sbom_dict)
        except Exception as _sbe:
            _log.warning("Could not parse SBOM document: %s", _sbe)

    # ------------------------------------------------------------------
    # Step 2: Static analysis
    # ------------------------------------------------------------------
    all_findings: list[Finding] = []
    tool_status: dict[str, str] = {}

    if "analyze" in enabled_steps and sbom_dict:
        typer.echo("[2/4] Running static analysis …")
        try:
            from nuguard.analysis.static_analyzer import StaticAnalyzer  # noqa: PLC0415
            from nuguard.sbom.models import AiSbomDocument  # noqa: PLC0415

            # Inject a user-supplied container image into the SBOM so Trivy
            # and Grype pick it up without needing it to appear in source.
            if container_image:
                sbom_dict = {
                    **sbom_dict,
                    "nodes": list(sbom_dict.get("nodes") or []) + [{
                        "id": "_cli_image",
                        "name": container_image,
                        "component_type": "CONTAINER_IMAGE",
                        "metadata": {"base_image": container_image},
                    }],
                }

            if sbom_doc is None:
                sbom_doc = AiSbomDocument.model_validate(sbom_dict)

            atlas_config: dict[str, Any] = {}
            if llm:
                atlas_config["llm"] = True

            analyzer = StaticAnalyzer(
                enable_atlas=not no_atlas,
                enable_osv=not no_osv,
                enable_grype=not no_grype,
                enable_checkov=not no_checkov,
                enable_trivy=not no_trivy,
                enable_semgrep=not no_semgrep,
                enable_supply_chain=not no_supply_chain,
                source_path=src_path,
                atlas_config=atlas_config,
                min_severity=Severity.INFO,
                supply_chain_profile=supply_chain_profile,
                supply_chain_verify_artifacts=supply_chain_verify,
            )
            all_findings = analyzer.analyze(sbom_doc)
            tool_status["nga-rules"] = "ok"
            tool_status["osv"] = "ok" if not no_osv else "skipped"
            tool_status["grype"] = _detect_tool_status("grype", not no_grype)
            tool_status["checkov"] = _detect_tool_status("checkov", not no_checkov)
            tool_status["trivy"] = _detect_tool_status("trivy", not no_trivy)
            tool_status["semgrep"] = _detect_tool_status("semgrep", not no_semgrep)
            tool_status["atlas"] = "ok" if not no_atlas else "skipped"
            tool_status["supply-chain"] = "ok" if not no_supply_chain else "skipped"
            typer.echo(f"    ✓ Analysis complete: {len(all_findings)} finding(s)")
        except Exception as exc:
            typer.echo(f"error: analysis failed: {exc}", err=True)
            _log.exception("static analysis failed")
            raise typer.Exit(code=3)

    elif "analyze" not in enabled_steps:
        typer.echo("[2/4] Analysis step skipped")

    # ------------------------------------------------------------------
    # Step 3: Policy check (optional)
    # ------------------------------------------------------------------
    if "policy" in enabled_steps and policy:
        typer.echo("[3/4] Checking Cognitive Policy …")
        _policy_path = Path(policy)
        if not _policy_path.exists():
            typer.echo(
                f"    ⚠ policy file not found: {policy} — policy step skipped",
                err=True,
            )
        elif sbom_doc is None:
            typer.echo("    ⚠ SBOM not available — policy step skipped", err=True)
        else:
            try:
                from nuguard.policy.checker import check_policy_against_sbom  # noqa: PLC0415
                from nuguard.policy.parser import parse_policy  # noqa: PLC0415
                _policy_text = _policy_path.read_text(encoding="utf-8")
                _cognitive_policy = parse_policy(_policy_text)
                policy_result = check_policy_against_sbom(_cognitive_policy, sbom_doc)
                _gap_count = len(policy_result.gaps)
                typer.echo(f"    ✓ Policy check complete: {_gap_count} gap(s)")
                if _gap_count:
                    typer.echo(f"    ⚠ {_gap_count} policy gap(s) found", err=True)
            except Exception as _pe:
                typer.echo(f"    ⚠ Policy check failed: {_pe}", err=True)
                _log.exception("policy check failed")
    else:
        typer.echo("[3/4] Policy step skipped")

    # ------------------------------------------------------------------
    # Step 4: Redteam (optional)
    # ------------------------------------------------------------------
    _redteam_target: str | None = target or None
    # Fall back to SBOM-discovered deployment URL when --target not set
    if not _redteam_target and sbom_doc is not None and sbom_doc.summary:
        _rt_url = getattr(sbom_doc.summary, "app_url", None) or getattr(
            sbom_doc.summary, "deployment_url", None
        )
        if isinstance(_rt_url, str):
            _redteam_target = _rt_url
    if "redteam" in enabled_steps and _redteam_target:
        typer.echo(f"[4/4] Running red-team against {_redteam_target} …")
        if sbom_doc is None:
            typer.echo("    ⚠ SBOM not available — redteam step skipped", err=True)
        else:
            try:
                import asyncio as _asyncio  # noqa: PLC0415

                from nuguard.cli.commands.redteam import _run_redteam  # noqa: PLC0415
                (
                    _rt_findings, _, _rt_records, _rt_outcome, _rt_notes, _, _, _, _, _, _, _
                ) = _asyncio.run(
                    _run_redteam(
                        sbom_doc=sbom_doc,
                        sbom_path=sbom_path if sbom_path else None,
                        policy_path=Path(policy) if policy else None,
                        target_url=_redteam_target,
                        canary_path=None,
                        profile="ci",
                        min_impact_score=3.0,
                    )
                )
                for _note in _rt_notes:
                    typer.echo(f"    ⚠ {_note}", err=True)
                typer.echo(
                    f"    ✓ Red-team complete: {len(_rt_findings)} finding(s) (outcome: {_rt_outcome})"
                )
                all_findings.extend(_rt_findings)
            except Exception as _rte:
                typer.echo(f"    ⚠ Red-team step failed: {_rte}", err=True)
                _log.exception("redteam step failed")
    elif "redteam" in enabled_steps and not _redteam_target:
        typer.echo(
            "[4/4] Redteam step skipped — no target URL (use --target or ensure SBOM has deployment URL)",
            err=True,
        )
    else:
        typer.echo("[4/4] Redteam step skipped")

    # ------------------------------------------------------------------
    # Write outputs
    # ------------------------------------------------------------------
    _write_outputs(all_findings, out_dir, sbom_path, src_path)

    # ------------------------------------------------------------------
    # Terminal summary
    # ------------------------------------------------------------------
    from nuguard.analysis.plugins.terminal_reporter import (
        print_terminal_report,  # noqa: PLC0415
    )
    print_terminal_report(
        findings=all_findings,
        tool_status=tool_status,
        scan_target=str(src_path),
    )

    # ------------------------------------------------------------------
    # Exit code
    # ------------------------------------------------------------------
    _exit(all_findings, fail_sev)


def _detect_tool_status(tool: str, enabled: bool) -> str:
    if not enabled:
        return "skipped"
    import shutil  # noqa: PLC0415
    return "ok" if shutil.which(tool) else "skipped"



def _write_outputs(
    findings: list[Finding],
    out_dir: Path,
    sbom_path: Path,
    src_path: Path,
) -> None:
    """Write findings.json, findings.sarif, and report.md."""
    # findings.json
    findings_json = out_dir / "findings.json"
    data = {
        "total": len(findings),
        "findings": [f.model_dump() for f in findings],
    }
    findings_json.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    typer.echo(f"    ✓ findings.json     → {findings_json}")

    # findings.sarif
    findings_sarif = out_dir / "findings.sarif"
    try:
        from nuguard.output.sarif_generator import generate_sarif  # noqa: PLC0415
        sarif_str = generate_sarif(findings, sbom_path=sbom_path, scan_target=src_path)
        findings_sarif.write_text(sarif_str, encoding="utf-8")
        typer.echo(f"    ✓ findings.sarif    → {findings_sarif}")
    except Exception as exc:
        _log.warning("SARIF generation failed: %s", exc)

    # report.md
    report_md = out_dir / "report.md"
    try:
        from nuguard.cli.commands.analyze import _render_markdown  # noqa: PLC0415
        md = _render_markdown(findings, sbom_path, min_severity="info")
        report_md.write_text(md, encoding="utf-8")
        typer.echo(f"    ✓ report.md         → {report_md}")
    except Exception as exc:
        _log.warning("Markdown report generation failed: %s", exc)


def _exit(findings: list[Finding], fail_sev: str) -> None:
    """Compute and apply the correct exit code."""
    has_critical = any(f.severity == Severity.CRITICAL for f in findings)
    fail_rank = _SEV_ORDER.get(fail_sev, 1)
    has_threshold = any(
        _SEV_ORDER.get(f.severity.value, 99) <= fail_rank
        for f in findings
    )

    if has_critical:
        raise typer.Exit(code=2)
    if has_threshold:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)
