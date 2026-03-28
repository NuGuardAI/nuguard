"""``nuguard policy`` sub-commands: validate, check, show."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

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

    The resulting JSON is the canonical control list used by ``nuguard validate``
    and ``nuguard redteam`` for consistent, reproducible treatment of policy rules.
    """
    from nuguard.config import load_config
    from nuguard.policy.compiler import compile_controls
    from nuguard.policy.loader import compiled_path_for, save_controls

    cfg = load_config(config_file)

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

        llm_client = LLMClient(
            model=cfg.litellm_model,
            api_key=cfg.litellm_api_key,
        )

    text = resolved_policy.read_text(encoding="utf-8")
    _console.print(
        f"Compiling [bold]{resolved_policy.name}[/bold] "
        f"({'LLM-assisted' if effective_llm and llm_client else 'rule-based'}) …"
    )

    try:
        controls = asyncio.run(
            compile_controls(text, use_llm=effective_llm, llm_client=llm_client)
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
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text | json | markdown.",
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

    # Fall back to nuguard.yaml for --sbom and --policy when not provided on CLI
    cfg = load_config(config_file)
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

    all_findings: list[dict] = []
    has_critical = False

    # ---- Policy vs SBOM cross-check ----------------------------------------
    if policy_obj is not None and doc is not None:
        from nuguard.policy.checker import check_policy_against_sbom

        gaps = check_policy_against_sbom(policy_obj, doc)
        for gap in gaps:
            all_findings.append(
                {
                    "source": "policy_check",
                    "id": gap.check_id,
                    "severity": gap.severity,
                    "section": gap.policy_section,
                    "component": gap.sbom_component,
                    "message": gap.message,
                }
            )
            if gap.severity in ("high", "critical"):
                has_critical = True

        if output_format == "text":
            if gaps:
                table = Table(title="Policy ↔ SBOM Gaps", show_header=True)
                table.add_column("Check")
                table.add_column("Severity")
                table.add_column("Section")
                table.add_column("Message")
                for gap in gaps:
                    colour = "red" if gap.severity in ("high", "critical") else "yellow"
                    table.add_row(
                        gap.check_id,
                        f"[{colour}]{gap.severity}[/{colour}]",
                        gap.policy_section,
                        gap.message,
                    )
                _console.print(table)
            else:
                _console.print("[green]✓ No policy/SBOM gaps found.[/green]")

    # ---- Compliance framework assessment -----------------------------------
    if (framework or controls) and doc is not None:
        from nuguard.policy.assessment import run_compliance_assessment

        fw_name = framework or "custom"
        _console.print(f"\n[bold]Running compliance assessment:[/bold] {fw_name} …")

        try:
            assessment = asyncio.run(
                run_compliance_assessment(
                    doc,
                    framework=fw_name,
                    enable_llm=enable_llm,
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

        if output_format == "text":
            _print_assessment_table(assessment)

    # ---- JSON / Markdown output --------------------------------------------
    meta = ReportMeta(
        llm_models=[cfg.litellm_model] if enable_llm else [],
    )
    if output_format == "json":
        payload = {"_meta": meta.to_dict(), "findings": all_findings}
        content = json.dumps(payload, indent=2, default=str)
        if output_file:
            output_file.write_text(content, encoding="utf-8")
            _console.print(f"Output written to {output_file}")
        else:
            typer.echo(content)
    elif output_format == "markdown":
        content = _policy_findings_to_markdown(all_findings, meta)
        if output_file:
            output_file.write_text(content, encoding="utf-8")
            _console.print(f"Output written to {output_file}")
        else:
            typer.echo(content)
    else:
        _console.print(f"[dim]{meta.to_text_line()}[/dim]")

    if not all_findings:
        _console.print("[green]✓ No findings.[/green]")
        raise typer.Exit(code=_EXIT_CLEAN)

    raise typer.Exit(code=_EXIT_CRITICAL if has_critical else _EXIT_FINDINGS)


def _policy_findings_to_markdown(findings: list[dict], meta: ReportMeta | None = None) -> str:
    """Render policy check findings as a Markdown report string."""
    if meta is None:
        meta = ReportMeta()
    lines: list[str] = ["# NuGuard Policy Report", ""]
    lines += meta.to_markdown_lines()
    if not findings:
        lines += ["_No findings._", ""]
        return "\n".join(lines)

    lines += [f"**{len(findings)} finding(s)**", ""]
    for f in findings:
        source = f.get("source", "")
        if source == "policy_check":
            sev = (f.get("severity") or "info").upper()
            lines += [f"## [{sev}] Policy Gap: {f.get('id', '')}", ""]
            lines += [f"**Section:** {f.get('section', '')}", ""]
            lines += [f.get("message", ""), ""]
            if f.get("component"):
                lines += [f"**Component:** {f['component']}", ""]
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
    return "\n".join(lines)


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


@policy_app.command("show")
def show(
    policy_id: str = typer.Option(
        ...,
        "--policy-id",
        help="ID of the stored policy to display.",
    ),
) -> None:
    """Display a stored cognitive policy by ID."""
    _err_console.print(f"Policy '{policy_id}' not found.")
    raise typer.Exit(code=_EXIT_FINDINGS)
