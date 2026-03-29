"""nuguard validate — happy-path and policy compliance runner."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from nuguard.cli.report_meta import ReportMeta
from nuguard.models.validate import ValidateRunResult

validate_app = typer.Typer(name="validate", help="Validate AI application behaviour.")

_console = Console()
_err_console = Console(stderr=True)
_log = logging.getLogger(__name__)


@validate_app.callback(invoke_without_command=True)
def validate_command(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to nuguard.yaml"
    ),
    target: Optional[str] = typer.Option(
        None, "--target", help="Override validate.target URL"
    ),
    policy: Optional[Path] = typer.Option(
        None, "--policy", help="Path to Cognitive Policy Markdown"
    ),
    canary: Optional[Path] = typer.Option(
        None, "--canary", help="Path to canary.json seed file"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write findings JSON to this path"
    ),
    format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text | json | markdown"
    ),
    fail_on: str = typer.Option(
        "high",
        "--fail-on",
        help="Exit non-zero when any finding meets this severity: critical | high | medium | low",
    ),
    baseline: Optional[Path] = typer.Option(
        None, "--baseline", help="Path to a previous CapabilityMap JSON for regression detection"
    ),
) -> None:
    """Validate AI application happy-path behaviour and cognitive policy compliance.

    Runs capability probes, happy-path simulations, boundary assertions, and
    per-turn policy evaluations against the declared target.

    \b
    Examples:
      nuguard validate
      nuguard validate --target http://localhost:8000 --policy ./policy.md
      nuguard validate -c ./nuguard.yaml --output results.json --fail-on critical
    """
    if ctx.invoked_subcommand is not None:
        return
    _do_validate(
        config_path=config,
        target_override=target,
        policy_path=policy,
        canary_path=canary,
        output_path=output,
        fmt=format,
        fail_on=fail_on,
        baseline_path=baseline,
    )


def _do_validate(
    config_path: Optional[Path],
    target_override: Optional[str],
    policy_path: Optional[Path],
    canary_path: Optional[Path],
    output_path: Optional[Path],
    fmt: str,
    fail_on: str,
    baseline_path: Optional[Path],
) -> None:
    from nuguard.config import load_config  # noqa: PLC0415

    cfg = load_config(config_path)
    vc = cfg.validate_config

    # Apply overrides
    if target_override:
        vc = vc.model_copy(update={"target": target_override})

    if not vc.target:
        _err_console.print(
            "[red]Error:[/red] validate.target is not set. "
            "Add it to nuguard.yaml or pass --target."
        )
        raise typer.Exit(code=1)

    # Resolve policy path: --policy flag > config.policy_path
    resolved_policy_path: Optional[Path] = policy_path
    if resolved_policy_path is None and cfg.policy_path:
        resolved_policy_path = Path(cfg.policy_path)

    # Resolve canary path
    resolved_canary_path: Optional[Path] = canary_path
    if resolved_canary_path is None and vc.canary:
        resolved_canary_path = Path(vc.canary)

    auth_config = cfg.resolved_validate_auth_config()

    try:
        result: ValidateRunResult = asyncio.run(
            _run_validate(
                validate_config=vc,
                auth_config=auth_config,
                policy_path=resolved_policy_path,
                canary_path=resolved_canary_path,
                baseline_path=baseline_path,
            )
        )
    except Exception as exc:
        _err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=3) from exc

    # ── Output ────────────────────────────────────────────────────────────────
    meta = ReportMeta(
        llm_models=[cfg.litellm_model] if resolved_policy_path else [],
        verbose=vc.verbose,
    )
    if fmt == "json":
        payload = {"_meta": meta.to_dict(), **result.model_dump()}
        out = json.dumps(payload, indent=2, default=str)
        if output_path:
            output_path.write_text(out, encoding="utf-8")
            _console.print(f"[green]Results written to[/green] {output_path}")
        else:
            _console.print(out)
    elif fmt == "markdown":
        md = _validate_result_to_markdown(result, meta)
        if output_path:
            output_path.write_text(md, encoding="utf-8")
            _console.print(f"[green]Results written to[/green] {output_path}")
        else:
            _console.print(md)
    else:
        _print_validate_result(result, meta)
        if output_path:
            payload = {"_meta": meta.to_dict(), **result.model_dump()}
            output_path.write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            _console.print(f"\n[green]Results written to[/green] {output_path}")

    # ── Exit code ─────────────────────────────────────────────────────────────
    from nuguard.models.finding import Severity  # noqa: PLC0415

    severity_order = [s.value for s in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]]
    threshold_idx = severity_order.index(fail_on) if fail_on in severity_order else 1

    for f in result.findings:
        sev = f.get("severity", "info")
        if sev in severity_order and severity_order.index(sev) <= threshold_idx:
            raise typer.Exit(code=2)


async def _run_validate(
    validate_config: object,
    auth_config: object,
    policy_path: Optional[Path],
    canary_path: Optional[Path],
    baseline_path: Optional[Path],
) -> ValidateRunResult:
    from nuguard.models.validate import CapabilityMap  # noqa: PLC0415
    from nuguard.validate.runner import ValidateRunner  # noqa: PLC0415

    policy = None
    controls = None
    if policy_path is not None:
        if not policy_path.exists():
            raise FileNotFoundError(f"Policy file not found: {policy_path}")
        from nuguard.policy.parser import parse_policy  # noqa: PLC0415
        from nuguard.policy.loader import compiled_path_for, load_controls  # noqa: PLC0415

        policy = parse_policy(policy_path.read_text(encoding="utf-8"))

        compiled = compiled_path_for(policy_path)
        if compiled.exists():
            _log.info("Loading compiled policy controls from %s", compiled)
            controls = load_controls(compiled)
        else:
            _log.debug(
                "No compiled controls found at %s — using rule-based defaults", compiled
            )

    canary_config = None
    if canary_path is not None:
        if not canary_path.exists():
            raise FileNotFoundError(f"Canary file not found: {canary_path}")
        from nuguard.redteam.target.canary import CanaryConfig  # noqa: PLC0415
        canary_config = CanaryConfig.load(canary_path)

    baseline_map = None
    if baseline_path is not None:
        if not baseline_path.exists():
            raise FileNotFoundError(f"Baseline CapabilityMap not found: {baseline_path}")
        baseline_map = CapabilityMap.model_validate_json(baseline_path.read_text(encoding="utf-8"))

    runner = ValidateRunner(
        validate_config=validate_config,  # type: ignore[arg-type]
        auth_config=auth_config,  # type: ignore[arg-type]
        policy=policy,
        controls=controls,
        canary_config=canary_config,
        baseline_capability_map=baseline_map,
    )
    return await runner.run()


def _print_validate_result(result: "ValidateRunResult", meta: ReportMeta | None = None) -> None:
    if meta is None:
        meta = ReportMeta()
    _console.rule("[bold]Validate Results[/bold]")
    _console.print(f"[dim]{meta.to_text_line()}[/dim]")
    _console.print(
        f"Run ID: [dim]{result.run_id}[/dim]  "
        f"Scenarios: [bold]{result.scenarios_executed}[/bold]  "
        f"Outcome: [bold]{result.scan_outcome}[/bold]"
    )

    # Capability map table
    cm = result.capability_map
    if cm.entries:
        _console.print()
        tbl = Table(title="Capability Map", show_header=True, header_style="bold")
        tbl.add_column("Tool", style="cyan")
        tbl.add_column("Exercised")
        tbl.add_column("Calls")
        tbl.add_column("Policy OK")
        tbl.add_column("Exercised By")
        for entry in cm.entries:
            tbl.add_row(
                entry.tool_name,
                "[green]✓[/green]" if entry.exercised else "[red]✗[/red]",
                str(entry.calls_observed),
                "[green]✓[/green]" if entry.policy_compliant else "[red]✗[/red]",
                entry.exercised_by or "—",
            )
        tbl.caption = f"{cm.tools_exercised}/{cm.tools_total} tools exercised"
        _console.print(tbl)

    # Findings
    if result.findings:
        _console.print()
        _console.print(f"[bold red]{len(result.findings)} finding(s):[/bold red]")
        for f in result.findings:
            sev = f.get("severity", "?").upper()
            title = f.get("title", "Finding")
            goal = f.get("goal_type") or ""
            color = {
                "CRITICAL": "bright_red",
                "HIGH": "red",
                "MEDIUM": "yellow",
                "LOW": "blue",
            }.get(sev, "white")
            _console.print(
                f"  [{color}][{sev}][/{color}] {title}" + (f"  ({goal})" if goal else "")
            )
            desc = f.get("description", "")
            if desc:
                _console.print(f"    [dim]{desc[:200]}[/dim]")
    else:
        _console.print("\n[green]No findings — all validate scenarios passed.[/green]")

    # Verbose: print scenario traces grouped by scenario
    if meta.verbose and result.policy_records:
        from collections import OrderedDict  # noqa: PLC0415
        from rich.panel import Panel  # noqa: PLC0415

        _console.print()
        _console.rule("[bold]Scenario Traces[/bold]")
        groups: dict[tuple[str, str], list] = OrderedDict()
        for rec in result.policy_records:
            key = (rec.scenario_name or "unknown", rec.scenario_type or "")
            groups.setdefault(key, []).append(rec)

        for (sname, stype), records in groups.items():
            label = f"[bold magenta]{sname}[/bold magenta]"
            if stype:
                label += f"  [dim]({stype})[/dim]"
            _console.print(f"\n  {label}")
            for rec in records:
                _console.rule(
                    f"[cyan]turn {rec.turn}[/cyan]",
                    style="dim",
                )
                _console.print(
                    Panel(
                        rec.prompt,
                        title="[bold]→ REQUEST[/bold]",
                        title_align="left",
                        border_style="blue",
                        expand=True,
                    )
                )
                if rec.tool_calls:
                    tool_names = "  ".join(
                        tc.get("name") or tc.get("function", {}).get("name", "?")
                        for tc in rec.tool_calls
                    )
                    _console.print(f"  [dim]tool_calls:[/dim] [yellow]{tool_names}[/yellow]")
                _console.print(
                    Panel(
                        rec.response or "[dim](empty)[/dim]",
                        title="[bold]← RESPONSE[/bold]",
                        title_align="left",
                        border_style="green",
                        expand=True,
                    )
                )
                if rec.violations:
                    for v in rec.violations:
                        _console.print(
                            f"  [red]policy violation:[/red] [{v.get('severity','?').upper()}] "
                            f"{v.get('evidence', '')}"
                        )


def _validate_result_to_markdown(result: "ValidateRunResult", meta: ReportMeta | None = None) -> str:
    """Render a ValidateRunResult as a Markdown report string."""
    if meta is None:
        meta = ReportMeta()
    lines: list[str] = ["# NuGuard Validate Report", ""]
    lines += meta.to_markdown_lines()
    lines += [
        f"**Run ID:** {result.run_id}  ",
        f"**Scenarios executed:** {result.scenarios_executed}  ",
        f"**Outcome:** {result.scan_outcome}",
        "",
    ]

    # Capability map
    cm = result.capability_map
    if cm.entries:
        lines += [
            "## Capability Map",
            "",
            f"**{cm.tools_exercised}/{cm.tools_total} tools exercised**",
            "",
            "| Tool | Exercised | Calls | Policy OK | Exercised By |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in cm.entries:
            lines.append(
                f"| {entry.tool_name} "
                f"| {'✓' if entry.exercised else '✗'} "
                f"| {entry.calls_observed} "
                f"| {'✓' if entry.policy_compliant else '✗'} "
                f"| {entry.exercised_by or '—'} |"
            )
        lines += [""]

    # Findings
    if result.findings:
        lines += [f"## Findings ({len(result.findings)})", ""]
        for f in result.findings:
            sev = (f.get("severity") or "info").upper()
            title = f.get("title", "Finding")
            goal = f.get("goal_type") or ""
            lines += [f"### [{sev}] {title}" + (f" ({goal})" if goal else ""), ""]
            desc = f.get("description", "")
            if desc:
                lines += [desc, ""]
            comp = f.get("affected_component")
            if comp:
                lines += [f"**Component:** {comp}", ""]
            rem = f.get("remediation")
            if rem:
                lines += [f"**Remediation:** {rem}", ""]
    else:
        lines += ["## Findings", "", "_No findings — all validate scenarios passed._", ""]

    # Verbose: scenario traces grouped by scenario
    if meta.verbose and result.policy_records:
        lines += ["## Scenario Traces", ""]
        # Group records by (scenario_name, scenario_type) preserving insertion order
        from collections import OrderedDict  # noqa: PLC0415
        groups: dict[tuple[str, str], list] = OrderedDict()
        for rec in result.policy_records:
            key = (rec.scenario_name or "unknown", rec.scenario_type or "")
            groups.setdefault(key, []).append(rec)

        for (sname, stype), records in groups.items():
            header = f"### Scenario: `{sname}`"
            if stype:
                header += f" ({stype})"
            lines += [header, ""]
            for rec in records:
                lines += [f"#### Turn {rec.turn}", ""]
                lines += ["**Request:**", ""]
                lines += [f"```\n{rec.prompt}\n```", ""]
                lines += ["**Response:**", ""]
                lines += [f"```\n{rec.response or '(empty)'}\n```", ""]
                if rec.tool_calls:
                    tool_names = ", ".join(
                        tc.get("name") or tc.get("function", {}).get("name", "?")
                        for tc in rec.tool_calls
                    )
                    lines += [f"**Tool calls:** {tool_names}", ""]
                if rec.violations:
                    lines += ["**Policy violations:**", ""]
                    for v in rec.violations:
                        lines += [f"- [{v.get('severity','?').upper()}] {v.get('evidence','')}", ""]
                if rec.canary_hits:
                    lines += [f"**Canary hits:** {', '.join(rec.canary_hits)}", ""]

    return "\n".join(lines)
