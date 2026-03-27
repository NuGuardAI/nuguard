"""nuguard target — target connectivity and auth verification commands."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from nuguard.config import load_config
from nuguard.common.errors import TargetUnavailableError
from nuguard.common.auth import AuthConfig
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.models.health_report import TargetHealthReport
from nuguard.redteam.target.canary import CanaryConfig

target_app = typer.Typer(name="target", help="Target connectivity and auth verification.")
console = Console()


@target_app.command(name="verify")
def verify_command(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to nuguard.yaml"),
    ] = None,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Which config section to verify: redteam or validate"),
    ] = "redteam",
    target: Annotated[
        str | None,
        typer.Option("--target", help="Target base URL (overrides nuguard.yaml)"),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Chat endpoint path (overrides nuguard.yaml)"),
    ] = None,
    auth_header: Annotated[
        str | None,
        typer.Option("--auth-header", help="Auth header string e.g. 'Authorization: Bearer tok'"),
    ] = None,
    canary: Annotated[
        Path | None,
        typer.Option("--canary", help="Path to canary.json (verifies tenant tokens too)"),
    ] = None,
) -> None:
    """Verify authentication and connectivity against the target AI application.

    Sends a single probe request for each declared credential (default + canary tenants)
    and reports HTTP status, response time, and auth result. Exits non-zero if any
    non-skipped credential fails.

    Use --mode to select which nuguard.yaml section drives the check:
      --mode redteam   (default) reads redteam.target and redteam.auth
      --mode validate  reads validate.target and validate.auth

    Examples:
        nuguard target verify
        nuguard target verify --mode validate
        nuguard target verify --target http://localhost:3000 --auth-header "Authorization: Bearer $TOKEN"
        nuguard target verify --config nuguard.yaml --canary canary.json
    """
    if mode not in ("redteam", "validate"):
        console.print(f"[red]Error:[/red] --mode must be 'redteam' or 'validate', got '{mode}'")
        raise typer.Exit(code=1)
    asyncio.run(_verify_async(config, mode, target, endpoint, auth_header, canary))


async def _verify_async(
    config_path: Path | None,
    mode: str,
    target_override: str | None,
    endpoint_override: str | None,
    auth_header_override: str | None,
    canary_path: Path | None,
) -> None:
    # Load config
    cfg = load_config(config_path)

    # Resolve target URL and auth from the selected mode section
    target_url: str | None
    if mode == "validate":
        target_url = target_override or cfg.validate_config.target
        ep = endpoint_override or cfg.validate_config.target_endpoint or "/chat"
        if auth_header_override:
            auth = AuthConfig.from_header_string(auth_header_override)
        else:
            auth = cfg.resolved_validate_auth_config()
        mode_label = "validate"
    else:
        target_url = target_override or cfg.target_url
        ep = endpoint_override or cfg.target_endpoint or "/chat"
        if auth_header_override:
            auth = AuthConfig.from_header_string(auth_header_override)
        else:
            auth = cfg.resolved_auth_config()
        mode_label = "redteam"

    if not target_url:
        section = "validate.target" if mode == "validate" else "redteam.target"
        console.print(f"[red]Error:[/red] No target URL. Set {section} in nuguard.yaml or pass --target.")
        raise typer.Exit(code=1)

    # Load canary (for tenant token verification)
    # Prefer explicit --canary flag, then mode-specific config, then top-level canary_path.
    canary_config: CanaryConfig | None = None
    if canary_path:
        canary_file: Path | None = canary_path
    elif mode == "validate" and cfg.validate_config.canary:
        canary_file = Path(cfg.validate_config.canary)
    elif cfg.canary_path:
        canary_file = Path(cfg.canary_path)
    else:
        canary_file = None

    if canary_file and canary_file.exists():
        canary_config = CanaryConfig.load(canary_file)
    elif canary_file:
        console.print(f"[yellow]Warning:[/yellow] canary file not found: {canary_file}")

    console.print(f"\n[bold]NuGuard Target Verify[/bold]  (mode: {mode_label})")
    console.print(f"  Target:   {target_url}{ep}")
    console.print(f"  Auth:     {auth.type}")
    if canary_config:
        tenant_count = len([t for t in canary_config.tenants if t.session_token])
        console.print(f"  Tenants:  {tenant_count} with session_token in canary.json")
    console.print()

    # Run bootstrap
    try:
        bootstrapper = AuthBootstrapper(
            target_url=target_url,
            endpoint=ep,
            default_auth=auth,
            canary_config=canary_config,
        )
        report: TargetHealthReport = await bootstrapper.run()
    except TargetUnavailableError as exc:
        console.print(f"[red]✗ Target unavailable:[/red] {exc}")
        raise typer.Exit(code=2)

    # Render results table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Identity", style="cyan")
    table.add_column("Auth Type")
    table.add_column("Status")
    table.add_column("HTTP", justify="right")
    table.add_column("Time (ms)", justify="right")
    table.add_column("Detail")

    status_styles = {
        "ok": "green",
        "auth_failed": "red",
        "target_unavailable": "red",
        "skipped": "dim",
    }

    for check in report.checks:
        style = status_styles.get(check.status, "white")
        table.add_row(
            check.identity,
            check.auth_type,
            f"[{style}]{check.status}[/{style}]",
            str(check.http_status_code) if check.http_status_code else "—",
            f"{check.response_time_ms:.0f}" if check.response_time_ms else "—",
            check.error_detail[:60] if check.error_detail else "",
        )

    console.print(table)

    if report.all_ok:
        console.print("\n[green]All credentials verified successfully.[/green]")
        raise typer.Exit(code=0)
    else:
        failed = report.failed_checks
        console.print(f"\n[red]{len(failed)} credential(s) failed verification.[/red]")
        for f in failed:
            if f.status == "auth_failed":
                console.print(
                    f"  → [cyan]{f.identity}[/cyan]: authentication rejected "
                    f"(HTTP {f.http_status_code}). Check credentials."
                )
            else:
                console.print(
                    f"  → [cyan]{f.identity}[/cyan]: target unreachable. "
                    f"Is the app running at {target_url}?"
                )
        raise typer.Exit(code=1)
