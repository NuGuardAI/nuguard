"""``nuguard policy`` sub-commands: validate, check, show."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

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
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
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
        help="Output format: text | json.",
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
      nuguard policy check --file policy.md --sbom app.sbom.json

    \b
      # Compliance framework assessment
      nuguard policy check --sbom app.sbom.json --framework owasp-llm-top10

    \b
      # Both at once, with LLM enrichment
      nuguard policy check --file policy.md --sbom app.sbom.json --framework owasp-llm-top10 --llm
    """
    from nuguard.sbom.extractor.serializer import AiSbomSerializer

    if not sbom and not file:
        _err_console.print("Provide at least --sbom or --file.")
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
    if file:
        if not file.exists():
            _err_console.print(f"Policy file not found: {file}")
            raise typer.Exit(code=_EXIT_ERROR)
        try:
            from nuguard.policy.parser import parse_policy

            policy_obj = parse_policy(file.read_text(encoding="utf-8"))
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

    # ---- JSON output -------------------------------------------------------
    if output_format == "json":
        _console.print(json.dumps(all_findings, indent=2, default=str))

    if not all_findings:
        _console.print("[green]✓ No findings.[/green]")
        raise typer.Exit(code=_EXIT_CLEAN)

    raise typer.Exit(code=_EXIT_CRITICAL if has_critical else _EXIT_FINDINGS)


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
    try:
        from nuguard.db.local import LocalDb

        db = LocalDb()
        stored = db.get_policy(policy_id)
    except Exception as exc:
        _err_console.print(f"Error accessing local database: {exc}")
        raise typer.Exit(code=_EXIT_ERROR) from exc

    if stored is None:
        _err_console.print(f"Policy '{policy_id}' not found.")
        raise typer.Exit(code=_EXIT_FINDINGS)

    _console.print(json.dumps(stored, indent=2, default=str))
