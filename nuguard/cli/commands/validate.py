"""nuguard validate — happy-path and policy compliance runner."""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from nuguard.cli.common import output_path_for_format, parse_output_formats
from nuguard.cli.report_meta import ReportMeta
from nuguard.common.logging import get_logger
from nuguard.models.validate import ValidateRunResult

validate_app = typer.Typer(name="validate", help="Validate AI application behaviour.")

_console = Console()
_err_console = Console(stderr=True)
_log = get_logger(__name__)

_MAX_TRACE_TURNS_PER_SCENARIO = 4
_MAX_TRACE_SNIPPET_CHARS = 800
_MAX_TRACE_EVIDENCE_LINES = 4


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
    format: list[str] | None = typer.Option(
        None,
        "--format",
        "-f",
        help=(
            "Output format(s): text | json | markdown. "
            "Repeat --format or pass comma-separated values."
        ),
    ),
    fail_on: str = typer.Option(
        "high",
        "--fail-on",
        help="Exit non-zero when any finding meets this severity: critical | high | medium | low",
    ),
    baseline: Optional[Path] = typer.Option(
        None, "--baseline", help="Path to a previous CapabilityMap JSON for regression detection"
    ),
    verbose: Optional[bool] = typer.Option(
        None,
        "--verbose/--no-verbose",
        "-v/-V",
        help="Print detailed turn traces. Overrides validate.verbose in nuguard.yaml.",
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
    try:
        formats = parse_output_formats(
            format,
            default_format="text",
            allowed_formats={"text", "json", "markdown"},
        )
    except ValueError as exc:
        _err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=2)

    if len(formats) > 1 and output is None:
        _err_console.print(
            "[red]Error:[/red] --output is required when multiple --format values are requested"
        )
        raise typer.Exit(code=2)

    _do_validate(
        config_path=config,
        target_override=target,
        policy_path=policy,
        canary_path=canary,
        output_path=output,
        formats=formats,
        fail_on=fail_on,
        baseline_path=baseline,
        verbose=verbose,
    )


def _do_validate(
    config_path: Optional[Path],
    target_override: Optional[str],
    policy_path: Optional[Path],
    canary_path: Optional[Path],
    output_path: Optional[Path],
    formats: list[str],
    fail_on: str,
    baseline_path: Optional[Path],
    verbose: Optional[bool],
) -> None:
    from nuguard.config import load_config  # noqa: PLC0415

    try:
        cfg = load_config(config_path)
    except Exception as exc:
        _err_console.print(f"[red]Error:[/red] failed to load config: {exc}")
        raise typer.Exit(code=1) from exc
    vc = cfg.validate_config

    # Apply overrides
    if target_override:
        vc = vc.model_copy(update={"target": target_override})

    effective_verbose = verbose if verbose is not None else vc.verbose

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

    # Resolve SBOM path for endpoint discovery
    sbom_path: Optional[Path] = None
    if cfg.sbom_path:
        candidate = Path(cfg.sbom_path)
        if candidate.exists():
            sbom_path = candidate

    try:
        result: ValidateRunResult = asyncio.run(
            _run_validate(
                validate_config=vc,
                auth_config=auth_config,
                policy_path=resolved_policy_path,
                canary_path=resolved_canary_path,
                baseline_path=baseline_path,
                sbom_path=sbom_path,
            )
        )
    except Exception as exc:
        _err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=3) from exc

    # ── Output ────────────────────────────────────────────────────────────────
    meta = ReportMeta(
        llm_models=[cfg.litellm_model] if resolved_policy_path else [],
        verbose=effective_verbose,
        target_url=vc.target,
        target_endpoint=result.effective_endpoint or vc.target_endpoint or "/chat",
        effective_endpoint=result.effective_endpoint or vc.target_endpoint or "/chat",
        target_endpoint_source=result.target_endpoint_source or ("config" if vc.target_endpoint else "default"),
    )
    extension_map = {
        "text": ".txt",
        "json": ".json",
        "markdown": ".md",
    }

    def _render(fmt: str) -> str:
        if fmt == "json":
            payload = {"_meta": meta.to_dict(), **result.model_dump()}
            if meta.verbose:
                payload["diagnostics"] = _build_validate_diagnostics(result)
            return json.dumps(payload, indent=2, default=str)
        if fmt == "markdown":
            return _validate_result_to_markdown(result, meta)
        return _validate_result_to_text(result, meta)

    if output_path:
        for fmt in formats:
            out_path = output_path_for_format(
                output_path,
                fmt=fmt,
                all_formats=formats,
                extension_map=extension_map,
            )
            # Auto-create the parent directory, consistent with analyze /
            # behavior / redteam (issue #233) and scan / sbom generate.
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_render(fmt), encoding="utf-8")
            _console.print(f"[green]Results written to[/green] {out_path}")
    else:
        fmt = formats[0]
        if fmt == "text":
            _print_validate_result(result, meta)
        else:
            _console.print(_render(fmt))

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
    sbom_path: Optional[Path] = None,
) -> ValidateRunResult:
    from nuguard.models.validate import CapabilityMap  # noqa: PLC0415
    from nuguard.validate.runner import ValidateRunner  # noqa: PLC0415

    # Load SBOM when available — used for endpoint auto-discovery
    sbom = None
    if sbom_path is not None and sbom_path.exists():
        try:
            from nuguard.sbom.serializer import AiSbomSerializer  # noqa: PLC0415
            sbom = AiSbomSerializer.from_json(sbom_path.read_text(encoding="utf-8"))
            _log.debug("Loaded SBOM from %s for endpoint discovery", sbom_path)
        except Exception as exc:
            _log.debug("Could not load SBOM for endpoint discovery: %s", exc)

    policy = None
    controls = None
    if policy_path is not None:
        if not policy_path.exists():
            raise FileNotFoundError(f"Policy file not found: {policy_path}")
        from nuguard.policy.loader import compiled_path_for, load_controls  # noqa: PLC0415
        from nuguard.policy.parser import parse_policy  # noqa: PLC0415

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
        sbom=sbom,
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

    # Verbose: bounded diagnostics appendix
    if meta.verbose and result.policy_records:
        lines += ["## Diagnostics", "", "### Scenario Traces", ""]
        groups = _group_policy_records(result.policy_records)

        for (sname, stype), records in groups.items():
            header = f"#### Scenario: `{sname}`"
            if stype:
                header += f" ({stype})"
            lines += [header, ""]
            for rec in records[:_MAX_TRACE_TURNS_PER_SCENARIO]:
                lines += [f"##### Turn {rec.turn}", ""]
                lines += ["**Request:**", ""]
                lines += [f"```\n{_truncate_diag_text(rec.prompt)}\n```", ""]
                lines += ["**Response:**", ""]
                lines += [f"```\n{_truncate_diag_text(rec.response or '(empty)')}\n```", ""]
                if rec.tool_calls:
                    tool_names = ", ".join(
                        tc.get("name") or tc.get("function", {}).get("name", "?")
                        for tc in rec.tool_calls
                    )
                    lines += [f"**Tool calls:** {tool_names}", ""]
                if rec.violations:
                    lines += ["**Policy violations:**", ""]
                    for v in rec.violations[:_MAX_TRACE_EVIDENCE_LINES]:
                        lines += [
                            f"- [{v.get('severity','?').upper()}] "
                            f"{_truncate_diag_text(str(v.get('evidence', '')))}"
                        ]
                    lines += [""]
                if rec.canary_hits:
                    lines += [
                        f"**Canary hits:** {', '.join(rec.canary_hits[:_MAX_TRACE_EVIDENCE_LINES])}",
                        "",
                    ]
            if len(records) > _MAX_TRACE_TURNS_PER_SCENARIO:
                lines += [
                    f"_... [truncated] {len(records) - _MAX_TRACE_TURNS_PER_SCENARIO} additional turn(s)_",
                    "",
                ]

    return "\n".join(lines)


def _validate_result_to_text(result: "ValidateRunResult", meta: ReportMeta | None = None) -> str:
    """Render a ValidateRunResult as plain text."""
    if meta is None:
        meta = ReportMeta()

    lines = [
        "Validate Results",
        meta.to_text_line(),
        f"Run ID: {result.run_id}",
        f"Scenarios: {result.scenarios_executed}",
        f"Outcome: {result.scan_outcome}",
        "",
    ]

    if result.findings:
        lines.append("Findings:")
        for f in result.findings:
            sev = str(f.get("severity", "info")).upper()
            title = str(f.get("title", "Untitled finding"))
            lines.append(f"- [{sev}] {title}")
    else:
        lines.append("No findings.")

    return "\n".join(lines)


def _truncate_diag_text(value: str) -> str:
    text = value or ""
    if len(text) <= _MAX_TRACE_SNIPPET_CHARS:
        return text
    return text[:_MAX_TRACE_SNIPPET_CHARS] + "... [truncated]"


def _group_policy_records(policy_records: list) -> "OrderedDict[tuple[str, str], list]":
    groups: "OrderedDict[tuple[str, str], list]" = OrderedDict()
    for rec in policy_records:
        key = (rec.scenario_name or "unknown", rec.scenario_type or "")
        groups.setdefault(key, []).append(rec)
    return groups


def _build_validate_diagnostics(result: "ValidateRunResult") -> dict:
    groups = _group_policy_records(result.policy_records)
    scenario_traces: list[dict] = []
    for (sname, stype), records in groups.items():
        turns = []
        for rec in records[:_MAX_TRACE_TURNS_PER_SCENARIO]:
            turns.append(
                {
                    "turn": rec.turn,
                    "request": _truncate_diag_text(rec.prompt),
                    "response": _truncate_diag_text(rec.response or ""),
                    "tool_calls": rec.tool_calls,
                    "violations": [
                        {
                            "severity": v.get("severity", "?"),
                            "evidence": _truncate_diag_text(str(v.get("evidence", ""))),
                        }
                        for v in rec.violations[:_MAX_TRACE_EVIDENCE_LINES]
                    ],
                    "canary_hits": rec.canary_hits[:_MAX_TRACE_EVIDENCE_LINES],
                }
            )
        scenario_traces.append(
            {
                "scenario_name": sname,
                "scenario_type": stype,
                "turns": turns,
                "turns_truncated": max(0, len(records) - _MAX_TRACE_TURNS_PER_SCENARIO),
            }
        )
    return {
        "execution_notes": {
            "turns_per_scenario_cap": _MAX_TRACE_TURNS_PER_SCENARIO,
            "snippet_char_cap": _MAX_TRACE_SNIPPET_CHARS,
            "evidence_lines_cap": _MAX_TRACE_EVIDENCE_LINES,
        },
        "scenario_traces": scenario_traces,
    }
