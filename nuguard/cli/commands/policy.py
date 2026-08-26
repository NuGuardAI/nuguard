"""``nuguard policy`` sub-commands: validate, check, show."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from nuguard.cli.common import output_path_for_format, parse_output_formats
from nuguard.cli.report_meta import ReportMeta
from nuguard.common.logging import get_logger

_log = get_logger(__name__)
_console = Console()
_err_console = Console(stderr=True, style="bold red")

policy_app = typer.Typer(
    help="Cognitive policy linting, SBOM cross-checking, and compliance assessment.",
    no_args_is_help=True,
)

# Exit codes
_EXIT_CLEAN = 0
_EXIT_FINDINGS = 1
_EXIT_CRITICAL = 2
_EXIT_ERROR = 3
_MAX_POLICY_DIAG_EVIDENCE_LINES = 4
_MAX_POLICY_DIAG_SNIPPET_CHARS = 800


@policy_app.command("compile")
def compile_policy(
    policy_file: Optional[Path] = typer.Option(
        None,
        "--policy",
        "-p",
        help="Cognitive policy Markdown file. Falls back to config policy path.",
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="nuguard.yaml config file."
    ),
    use_llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Use LLM to generate richer test and boundary prompts.",
    ),
    output_file: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination JSON file. Default: <policy>.json alongside the .md file.",
    ),
) -> None:
    """Compile a cognitive policy Markdown file into structured JSON controls.

    The resulting JSON is the canonical control list used by ``nuguard behavior``
    and ``nuguard redteam`` for consistent, reproducible treatment of policy rules.
    """
    from nuguard.config import load_config
    from nuguard.policy.compiler import compile_controls
    from nuguard.policy.loader import compiled_path_for, save_controls
    from nuguard.policy.sbom_provenance import build_component_evidence

    try:
        cfg = load_config(config_file)
    except Exception as exc:
        _err_console.print(f"Error: failed to load config: {exc}")
        raise typer.Exit(code=_EXIT_ERROR) from exc

    resolved_policy: Optional[Path] = policy_file
    if resolved_policy is None and cfg.policy_path:
        resolved_policy = Path(cfg.policy_path)
    if resolved_policy is None:
        _err_console.print(
            "Error: no policy file specified. Use --policy or set 'policy' in nuguard.yaml."
        )
        raise typer.Exit(code=_EXIT_ERROR)
    if not resolved_policy.exists():
        _err_console.print(f"Error: policy file not found: {resolved_policy}")
        raise typer.Exit(code=_EXIT_ERROR)

    effective_llm = use_llm or cfg.policy_use_llm

    llm_client = None
    if effective_llm:
        from nuguard.common.llm_client import LLMClient

        _model = cfg.litellm_model or ""
        llm_client = LLMClient(
            model=cfg.litellm_model,
            api_key=cfg.litellm_api_key,
            api_base=cfg.litellm_api_base if _model.startswith("azure") else None,
        )

    component_evidence = []
    if cfg.sbom_path:
        component_evidence = build_component_evidence(Path(cfg.sbom_path))

    text = resolved_policy.read_text(encoding="utf-8")
    _console.print(
        f"Compiling [bold]{resolved_policy.name}[/bold] "
        f"({'LLM-assisted' if effective_llm and llm_client else 'rule-based'}) …"
    )

    try:
        controls = asyncio.run(
            compile_controls(
                text,
                use_llm=effective_llm,
                llm_client=llm_client,
                component_evidence=component_evidence,
            )
        )
    except Exception as exc:
        _err_console.print(f"Error during compilation: {exc}")
        raise typer.Exit(code=_EXIT_ERROR) from exc

    dest = output_file or compiled_path_for(resolved_policy)
    save_controls(controls, dest)

    _console.print(
        f"[green]✓ {len(controls)} control(s) written to {dest}[/green]"
    )

    # Print a summary table
    table = Table(title="Compiled Controls", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Section")
    table.add_column("Severity")
    table.add_column("Description")
    for ctrl in controls:
        table.add_row(ctrl.id, ctrl.section, ctrl.severity, ctrl.description[:70])
    _console.print(table)


@policy_app.command("validate")
def validate(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Path to the cognitive policy Markdown file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Lint a cognitive policy file for completeness and common mistakes."""
    from nuguard.policy.parser import parse_policy
    from nuguard.policy.validator import LintIssue, lint_policy

    try:
        text = file.read_text(encoding="utf-8")
        policy = parse_policy(text)
    except Exception as exc:
        _err_console.print(f"Error reading policy file: {exc}")
        raise typer.Exit(code=_EXIT_ERROR) from exc

    issues: list[LintIssue] = lint_policy(policy)

    if not issues:
        _console.print(f"[green]✓ {file.name} — no policy issues found.[/green]")
        raise typer.Exit(code=_EXIT_CLEAN)

    table = Table(title=f"Policy Lint: {file.name}", show_header=True)
    table.add_column("Rule", style="bold")
    table.add_column("Severity")
    table.add_column("Message")

    has_error = False
    for issue in issues:
        colour = "red" if issue.severity == "error" else "yellow"
        table.add_row(
            issue.rule_id,
            f"[{colour}]{issue.severity}[/{colour}]",
            issue.message,
        )
        if issue.severity == "error":
            has_error = True

    _console.print(table)
    _console.print(
        f"\n[bold]{len(issues)} issue(s) found[/bold] "
        f"({sum(1 for i in issues if i.severity == 'error')} error(s), "
        f"{sum(1 for i in issues if i.severity == 'warning')} warning(s))"
    )

    raise typer.Exit(code=_EXIT_CRITICAL if has_error else _EXIT_FINDINGS)


@policy_app.command("check")
def check(
    policy: Optional[Path] = typer.Option(
        None,
        "--policy",
        "-p",
        help="Path to the cognitive policy Markdown file.",
        exists=False,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    sbom: Optional[Path] = typer.Option(
        None,
        "--sbom",
        help="Path to the AI-SBOM JSON file to cross-check against.",
        exists=False,
    ),
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Path to nuguard.yaml (default: ./nuguard.yaml).",
        exists=False,
    ),
    framework: Optional[str] = typer.Option(
        None,
        "--framework",
        help="Compliance framework: owasp-llm-top10 | nist-ai-rmf | eu-ai-act.",
    ),
    controls: Optional[Path] = typer.Option(
        None,
        "--controls",
        help="Path to a custom controls JSON file.",
        exists=False,
    ),
    output_format: list[str] | None = typer.Option(
        None,
        "--format",
        help=(
            "Output format(s): text | json | markdown. "
            "Repeat --format or pass comma-separated values."
        ),
    ),
    output_file: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write output to this file instead of stdout.",
    ),
    enable_llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Enable LLM fallback for controls that cannot be assessed from SBOM alone.",
    ),
    verbose: Optional[bool] = typer.Option(
        None,
        "--verbose/--no-verbose",
        "-v/-V",
        help="Show additional diagnostics in output without changing findings.",
    ),
) -> None:
    """Cross-check policy against SBOM or run a compliance framework assessment.

    Examples:

    \b
      # Policy vs SBOM gap check
      nuguard policy check --policy policy.md --sbom app.sbom.json

    \b
      # Compliance framework assessment
      nuguard policy check --sbom app.sbom.json --framework owasp-llm-top10

    \b
      # Both at once, with LLM enrichment
      nuguard policy check --policy policy.md --sbom app.sbom.json --framework owasp-llm-top10 --llm

    \b
      # Read paths from nuguard.yaml
      nuguard policy check
    """
    from nuguard.config import load_config
    from nuguard.sbom.extractor.serializer import AiSbomSerializer

    try:
        cfg = load_config(config_file)
    except Exception as exc:
        _err_console.print(f"Error: failed to load config: {exc}")
        raise typer.Exit(code=_EXIT_ERROR) from exc

    try:
        formats = parse_output_formats(
            output_format,
            default_format="text",
            allowed_formats={"text", "json", "markdown"},
        )
    except ValueError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=_EXIT_ERROR)

    if len(formats) > 1 and output_file is None:
        _err_console.print(
            "Error: --output is required when multiple --format values are requested"
        )
        raise typer.Exit(code=_EXIT_ERROR)

    # Fall back to nuguard.yaml for --sbom and --policy when not provided on CLI
    if sbom is None and cfg.sbom_path:
        sbom = Path(cfg.sbom_path)
    if policy is None and cfg.policy_path:
        policy = Path(cfg.policy_path)

    if not sbom and not policy:
        _err_console.print("Provide --policy and/or --sbom (or set them in nuguard.yaml).")
        raise typer.Exit(code=_EXIT_ERROR)

    # Load SBOM
    doc = None
    if sbom:
        if not sbom.exists():
            _err_console.print(f"SBOM file not found: {sbom}")
            raise typer.Exit(code=_EXIT_ERROR)
        try:
            raw = sbom.read_text(encoding="utf-8")
            doc = AiSbomSerializer.from_json(raw)
        except Exception as exc:
            _err_console.print(f"Error reading SBOM: {exc}")
            raise typer.Exit(code=_EXIT_ERROR) from exc

    # Load policy
    policy_obj = None
    if policy:
        if not policy.exists():
            _err_console.print(f"Policy file not found: {policy}")
            raise typer.Exit(code=_EXIT_ERROR)
        try:
            from nuguard.policy.parser import parse_policy

            policy_obj = parse_policy(policy.read_text(encoding="utf-8"))
        except Exception as exc:
            _err_console.print(f"Error reading policy: {exc}")
            raise typer.Exit(code=_EXIT_ERROR) from exc

    effective_verbose = bool(verbose)

    all_findings: list[dict] = []
    diagnostics: dict[str, Any] = {}
    has_critical = False

    # ---- Policy vs SBOM cross-check ----------------------------------------
    if policy_obj is not None and doc is not None:
        from nuguard.policy.checker import PolicyCheckResult, check_policy_against_sbom

        check_result: PolicyCheckResult = check_policy_against_sbom(policy_obj, doc)
        for gap in check_result.gaps:
            all_findings.append(
                {
                    "source": "policy_check",
                    "id": gap.check_id,
                    "name": gap.name,
                    "description": gap.description,
                    "severity": gap.severity,
                    "section": gap.policy_section,
                    "component": gap.sbom_component,
                    "message": gap.message,
                    "searched": gap.searched,
                    "prompt_evidence": gap.prompt_evidence,
                    "remediation": gap.remediation,
                    "status": "gap",
                }
            )
            if gap.severity in ("high", "critical"):
                has_critical = True

        if effective_verbose:
            diagnostics["policy_check"] = _build_policy_check_diagnostics(check_result)

        if "text" in formats and output_file is None:
            _print_policy_check_result(check_result, verbose=effective_verbose)

    # ---- Compliance framework assessment -----------------------------------
    if (framework or controls) and doc is not None:
        from nuguard.policy.assessment import run_compliance_assessment

        fw_name = framework or "custom"
        _console.print(f"\n[bold]Running compliance assessment:[/bold] {fw_name} …")

        llm_client = None
        if enable_llm:
            from nuguard.common.llm_client import LLMClient

            _model = cfg.litellm_model or ""
            llm_client = LLMClient(
                model=cfg.litellm_model,
                api_key=cfg.litellm_api_key,
                api_base=cfg.litellm_api_base if _model.startswith("azure") else None,
            )

        try:
            assessment = asyncio.run(
                run_compliance_assessment(
                    doc,
                    framework=fw_name,
                    enable_llm=enable_llm,
                    llm=llm_client,
                )
            )
        except Exception as exc:
            _err_console.print(f"Assessment failed: {exc}")
            raise typer.Exit(code=_EXIT_ERROR) from exc

        from nuguard.models.policy import ComplianceResult

        for ev in assessment.evaluations:
            all_findings.append(
                {
                    "source": "compliance",
                    "id": ev.control.id,
                    "name": ev.control.name,
                    "result": ev.result.value,
                    "score": ev.score,
                    "severity": ev.control.severity,
                    "gaps": ev.gaps,
                    "remediation": ev.remediation,
                }
            )
            if ev.result == ComplianceResult.FAIL and ev.control.severity in (
                "critical",
                "high",
            ):
                has_critical = True

        if "text" in formats and output_file is None:
            _print_assessment_table(assessment)

    # ---- JSON / Markdown output --------------------------------------------
    meta = ReportMeta(
        llm_models=[cfg.litellm_model] if enable_llm else [],
        verbose=effective_verbose,
    )

    extension_map = {
        "text": ".txt",
        "json": ".json",
        "markdown": ".md",
    }

    def _render(fmt: str) -> str:
        if fmt == "json":
            payload = {"_meta": meta.to_dict(), "findings": all_findings}
            if meta.verbose and diagnostics:
                payload["diagnostics"] = diagnostics
            return json.dumps(payload, indent=2, default=str)
        if fmt == "markdown":
            return _policy_findings_to_markdown(all_findings, meta, diagnostics=diagnostics)
        return _policy_findings_to_text(all_findings, meta, diagnostics=diagnostics)

    if output_file:
        for fmt in formats:
            out_path = output_path_for_format(
                output_file,
                fmt=fmt,
                all_formats=formats,
                extension_map=extension_map,
            )
            # Auto-create the parent directory, consistent with analyze /
            # behavior / redteam (issue #233) and scan / sbom generate.
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_render(fmt), encoding="utf-8")
            _console.print(f"Output written to {out_path}")
    else:
        fmt = formats[0]
        if fmt == "text":
            _console.print(f"[dim]{meta.to_text_line()}[/dim]")
        else:
            typer.echo(_render(fmt))

    if not all_findings:
        _console.print("[green]✓ No findings.[/green]")
        raise typer.Exit(code=_EXIT_CLEAN)

    raise typer.Exit(code=_EXIT_CRITICAL if has_critical else _EXIT_FINDINGS)


def _print_policy_check_result(check_result: object, *, verbose: bool = False) -> None:
    """Print a PolicyCheckResult to the console."""
    from nuguard.policy.checker import PolicyCheckResult

    if not isinstance(check_result, PolicyCheckResult):
        return

    if verbose and check_result.passed:
        passed_table = Table(title="Satisfied Controls", show_header=True, show_lines=True)
        passed_table.add_column("Check", style="cyan", no_wrap=True)
        passed_table.add_column("Name")
        passed_table.add_column("Description")
        passed_table.add_column("Evidence")
        for ctrl in check_result.passed:
            evidence_text = "\n".join(ctrl.evidence[:3])
            if len(ctrl.evidence) > 3:
                evidence_text += f"\n… +{len(ctrl.evidence) - 3} more"
            passed_table.add_row(
                f"[green]{ctrl.check_id}[/green]",
                ctrl.name,
                ctrl.description[:60] + ("…" if len(ctrl.description) > 60 else ""),
                evidence_text,
            )
        _console.print(passed_table)

    if check_result.gaps:
        gap_table = Table(title="Policy ↔ SBOM Gaps", show_header=True, show_lines=verbose)
        gap_table.add_column("Check", no_wrap=True)
        gap_table.add_column("Name")
        gap_table.add_column("Severity")
        gap_table.add_column("Section")
        gap_table.add_column("Message")
        if verbose:
            gap_table.add_column("Details")

        for gap in check_result.gaps:
            colour = "red" if gap.severity in ("high", "critical") else "yellow"
            row = [
                f"[{colour}]{gap.check_id}[/{colour}]",
                gap.name,
                f"[{colour}]{gap.severity}[/{colour}]",
                gap.policy_section,
                gap.message,
            ]
            if verbose:
                details_lines: list[str] = []
                if gap.description:
                    details_lines.append(f"[dim]{gap.description[:80]}[/dim]")
                if gap.searched:
                    details_lines.append("[bold]Searched:[/bold]")
                    details_lines.extend(f"  · {s}" for s in gap.searched[:3])
                if gap.prompt_evidence:
                    details_lines.append("[bold]Prompt evidence:[/bold]")
                    details_lines.extend(f"  · {e}" for e in gap.prompt_evidence[:2])
                row.append("\n".join(details_lines))
            gap_table.add_row(*row)

        _console.print(gap_table)
    elif not verbose:
        _console.print("[green]✓ No policy/SBOM gaps found.[/green]")
    else:
        _console.print("[green]✓ No policy/SBOM gaps found.[/green]")


def _policy_findings_to_markdown(
    findings: list[dict],
    meta: ReportMeta | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """Render policy check findings as a Markdown report string."""
    if meta is None:
        meta = ReportMeta()
    lines: list[str] = ["# NuGuard Policy Report", ""]
    lines += meta.to_markdown_lines()
    if not findings:
        lines += ["_No findings._", ""]
        return "\n".join(lines)

    gap_count = sum(1 for f in findings if f.get("status") == "gap")
    passed_count = sum(1 for f in findings if f.get("status") == "passed")
    summary_parts = []
    if gap_count:
        summary_parts.append(f"**{gap_count} gap(s)**")
    if passed_count:
        summary_parts.append(f"**{passed_count} control(s) satisfied**")
    other_count = len(findings) - gap_count - passed_count
    if other_count:
        summary_parts.append(f"**{other_count} finding(s)**")
    lines += [", ".join(summary_parts) if summary_parts else f"**{len(findings)} finding(s)**", ""]
    for f in findings:
        source = f.get("source", "")
        if source == "policy_check":
            status = f.get("status", "gap")
            sev = (f.get("severity") or "info").upper()
            check_id = f.get("id", "")
            name = f.get("name", "")
            if status != "passed":
                heading = f"## [{sev}] {check_id}: {name}" if name else f"## [{sev}] Policy Gap: {check_id}"
                lines += [heading, ""]
                if f.get("description"):
                    lines += [f"_{f['description']}_", ""]
                lines += [f"**Section:** {f.get('section', '')}", ""]
                if f.get("message"):
                    lines += [f.get("message", ""), ""]
                if f.get("component"):
                    lines += [f"**Component:** {f['component']}", ""]
                if f.get("searched"):
                    lines += ["**Searched:**"]
                    for s in f["searched"]:
                        lines += [f"- {s}"]
                    lines += [""]
                if f.get("prompt_evidence"):
                    lines += ["**Prompt evidence (partial):**"]
                    for pe in f["prompt_evidence"]:
                        lines += [f"- {pe}"]
                    lines += [""]
                if f.get("remediation"):
                    lines += ["**Remediation:**", ""]
                    lines += [f"> {f['remediation']}", ""]
        elif source == "compliance":
            result = (f.get("result") or "").upper()
            sev = (f.get("severity") or "info").upper()
            lines += [f"## [{result}] {f.get('id', '')}: {f.get('name', '')}", ""]
            if f.get("gaps"):
                for gap in f["gaps"]:
                    lines += [f"- {gap}"]
                lines += [""]
            if f.get("remediation"):
                lines += [f"**Remediation:** {f['remediation']}", ""]

    if meta.verbose and diagnostics and diagnostics.get("policy_check"):
        policy_diag = diagnostics["policy_check"]
        lines += ["## Diagnostics", ""]
        lines += [
            f"_Evidence lines capped at {policy_diag.get('evidence_lines_cap', _MAX_POLICY_DIAG_EVIDENCE_LINES)} per control._",
            "",
        ]

        passed_controls = policy_diag.get("passed_controls", [])
        if passed_controls:
            lines += ["### Satisfied Controls", ""]
            for ctrl in passed_controls:
                lines += [f"#### [PASS] {ctrl.get('id', '')}: {ctrl.get('name', '')}", ""]
                if ctrl.get("description"):
                    lines += [f"_{ctrl['description']}_", ""]
                for ev in ctrl.get("evidence", []):
                    lines += [f"- {ev}"]
                lines += [""]
    return "\n".join(lines)


def _policy_findings_to_text(
    findings: list[dict],
    meta: ReportMeta | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """Render policy check findings as plain text."""
    if meta is None:
        meta = ReportMeta()
    lines = ["NuGuard Policy Report", meta.to_text_line(), ""]
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines)

    for f in findings:
        source = str(f.get("source", "")).strip() or "unknown"
        identifier = str(f.get("id", "")).strip()
        name = str(f.get("name", "")).strip()
        severity = str(f.get("severity", "info")).upper()
        status = str(f.get("status", "")).upper()
        title = f"[{severity}] {identifier}"
        if name:
            title += f": {name}"
        if status:
            title += f" ({status})"
        lines.append(title)
        lines.append(f"  source: {source}")
        message = str(f.get("message", "")).strip()
        if message:
            lines.append(f"  message: {message}")
        remediation = str(f.get("remediation", "")).strip()
        if remediation:
            lines.append(f"  remediation: {remediation}")
        lines.append("")

    if meta.verbose and diagnostics and diagnostics.get("policy_check"):
        passed_controls = diagnostics["policy_check"].get("passed_controls", [])
        lines.append("Diagnostics:")
        lines.append(f"- satisfied_controls: {len(passed_controls)}")
        lines.append(
            f"- evidence_lines_cap: {diagnostics['policy_check'].get('evidence_lines_cap', _MAX_POLICY_DIAG_EVIDENCE_LINES)}"
        )
        lines.append("")
    return "\n".join(lines)


def _truncate_policy_diag(value: str) -> str:
    text = value or ""
    if len(text) <= _MAX_POLICY_DIAG_SNIPPET_CHARS:
        return text
    return text[:_MAX_POLICY_DIAG_SNIPPET_CHARS] + "... [truncated]"


def _build_policy_check_diagnostics(check_result: object) -> dict[str, Any]:
    passed_controls: list[dict[str, Any]] = []
    for ctrl in getattr(check_result, "passed", []):
        passed_controls.append(
            {
                "id": ctrl.check_id,
                "name": ctrl.name,
                "description": _truncate_policy_diag(ctrl.description or ""),
                "policy_section": ctrl.policy_section,
                "evidence_source": ctrl.evidence_source,
                "evidence": [
                    _truncate_policy_diag(str(ev))
                    for ev in (ctrl.evidence or [])[:_MAX_POLICY_DIAG_EVIDENCE_LINES]
                ],
                "evidence_truncated": max(0, len(ctrl.evidence or []) - _MAX_POLICY_DIAG_EVIDENCE_LINES),
            }
        )

    return {
        "passed_controls": passed_controls,
        "evidence_lines_cap": _MAX_POLICY_DIAG_EVIDENCE_LINES,
        "snippet_char_cap": _MAX_POLICY_DIAG_SNIPPET_CHARS,
    }


def _print_assessment_table(assessment: object) -> None:
    from nuguard.models.policy import ComplianceResult, PolicyAssessmentResult

    if not isinstance(assessment, PolicyAssessmentResult):
        return

    _console.print(
        f"\n[bold]Compliance Assessment:[/bold] {assessment.framework}  "
        f"score=[bold]{assessment.score:.1%}[/bold]  "
        f"controls={assessment.total_controls}  "
        f"pass={assessment.pass_count}  "
        f"partial={assessment.partial_count}  "
        f"fail={assessment.fail_count}  "
        f"unable={assessment.unable_count}"
    )

    table = Table(title="Control Results", show_header=True)
    table.add_column("Control ID")
    table.add_column("Name")
    table.add_column("Severity")
    table.add_column("Result")
    table.add_column("Score", justify="right")

    _RESULT_COLOUR = {
        ComplianceResult.PASS.value: "green",
        ComplianceResult.PARTIAL.value: "yellow",
        ComplianceResult.FAIL.value: "red",
        ComplianceResult.UNABLE_TO_ASSESS.value: "dim",
        ComplianceResult.NOT_APPLICABLE.value: "dim",
    }

    for ev in assessment.evaluations:
        colour = _RESULT_COLOUR.get(ev.result.value, "white")
        table.add_row(
            ev.control.id,
            ev.control.name[:45],
            ev.control.severity,
            f"[{colour}]{ev.result.value}[/{colour}]",
            f"{ev.score:.2f}",
        )

    _console.print(table)


@policy_app.command("show", hidden=True)
def show(
    policy_id: str = typer.Option(
        ...,
        "--policy-id",
        help="ID of the stored policy to display.",
    ),
) -> None:
    """Display a stored cognitive policy by ID.

    .. deprecated::
        Always reports "not found" — there is no persisted-policy store.
        Kept as a hidden no-op stub so old automation scripts that invoke it
        don't get a Typer "no such command" error; the subcommand will be
        reintroduced once a policy registry exists (see issue #162).
    """
    _err_console.print(
        f"Policy '{policy_id}' not found. "
        "(Policy registry is not implemented yet; use `policy compile` to "
        "produce a JSON file alongside the source Markdown.)"
    )
    raise typer.Exit(code=_EXIT_FINDINGS)
