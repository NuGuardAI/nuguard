"""nuguard target discover-browser — Playwright browser-login discovery fallback.

Rescues ``nuguard target verify`` / pre-scan discovery (nuguard/common/discovery.py)
when the target app authenticates via an interactive redirect/OAuth flow (e.g. Auth0
Universal Login) that a plain HTTP client can't drive, and/or requires an extra chat
payload field (e.g. an opaque consumer/actor ID) that static discovery has no way to
guess. Drives a real (headless by default) browser through a generic-heuristics login
flow, captures the resulting session as a cookie_file NuGuard's existing AuthConfig
already supports, optionally sniffs the app's own outgoing chat request to discover
extra payload fields, and writes the results back into nuguard.yaml's ``target:``
block so ``behavior``/``redteam``/``target verify`` pick them up unchanged.

This is an explicit, opt-in command — never invoked automatically by verify/behavior/
redteam. Playwright and ruamel.yaml (the ``browser`` extra) are imported lazily by the
modules this command delegates to, so importing this file (and thus `nuguard --help`)
never requires them; only running the command does.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console

from nuguard.cli.commands.target import target_app
from nuguard.common.errors import BrowserLoginError, ConfigError
from nuguard.common.logging import get_logger
from nuguard.config import (  # noqa: PLC2701 — reuse existing env-interpolation
    _expand_env_vars,
    load_config,
)

_log = get_logger(__name__)
console = Console()

# BrowserLoginError.step -> exit code, matching the failure-mode table in the
# design plan. Anything not listed here (or a bare, non-BrowserLoginError
# exception) falls through to the generic-error branch, exit code 1.
_STEP_EXIT_CODES: dict[str, int] = {
    "playwright_not_installed": 3,
    "browser_binary_missing": 3,
    "navigate": 2,
}


@target_app.command(name="discover-browser")
def discover_browser_command(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to nuguard.yaml (must exist — this command writes to it)"),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--target", help="Target base URL (overrides nuguard.yaml target.url)"),
    ] = None,
    headed: Annotated[
        bool,
        typer.Option("--headed/--headless", help="Run with a visible browser window (default: headless)"),
    ] = False,
    timeout_s: Annotated[
        int,
        typer.Option("--timeout-s", help="Per-step browser timeout in seconds"),
    ] = 30,
    cookie_file: Annotated[
        Path | None,
        typer.Option("--cookie-file", help="Output path for the captured session cookies (default: ./cookies.txt next to nuguard.yaml)"),
    ] = None,
    sniff_chat: Annotated[
        bool,
        typer.Option("--sniff-chat/--no-sniff-chat", help="Attempt to discover extra chat payload fields (e.g. a consumer ID) by sniffing a live chat request"),
    ] = True,
    chat_message: Annotated[
        str,
        typer.Option("--chat-message", help="Message typed into the chat UI during payload-field sniffing"),
    ] = "Hello",
    write: Annotated[
        bool,
        typer.Option("--write/--dry-run", help="Apply the discovered auth/config to nuguard.yaml (default: dry-run, print the diff only)"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the interactive confirmation prompt when --write is set"),
    ] = False,
) -> None:
    """Log in via a real browser and rescue a broken nuguard.yaml target config.

    Use this when 'nuguard target verify' reports 'auth_failed' or pre-scan
    discovery reports 'no profile data extracted' because the app requires an
    interactive login (e.g. an Auth0/OAuth redirect) that a plain HTTP client
    can't drive. Captures the resulting session cookie and, unless
    --no-sniff-chat, discovers any extra fields (like an opaque consumer ID)
    the chat endpoint requires — then previews (or, with --write, applies) the
    corresponding update to nuguard.yaml's target.auth / target.chat_payload_extras.

    Requires the 'browser' extra: pip install "nuguard\\[browser]" && playwright install chromium

    Examples:
        nuguard target discover-browser --config nuguard.yaml
        nuguard target discover-browser --config nuguard.yaml --write --yes
        nuguard target discover-browser --config nuguard.yaml --headed --no-sniff-chat
    """
    asyncio.run(
        _discover_browser_async(
            config_path=config,
            target_override=target,
            headless=not headed,
            timeout_s=timeout_s,
            cookie_file_override=cookie_file,
            sniff_chat=sniff_chat,
            chat_message=chat_message,
            write=write,
            yes=yes,
        )
    )


async def _discover_browser_async(
    *,
    config_path: Path | None,
    target_override: str | None,
    headless: bool,
    timeout_s: int,
    cookie_file_override: Path | None,
    sniff_chat: bool,
    chat_message: str,
    write: bool,
    yes: bool,
) -> None:
    from nuguard.common.browser_login.config import BrowserDiscoveryConfig
    from nuguard.common.browser_login.public_api import BrowserDiscoveryRequest, discover_browser
    from nuguard.common.browser_login.yaml_writer import (
        apply_target_updates,
        dump_to_string,
        load_editable_yaml,
        render_diff,
        write_yaml,
    )

    resolved_config_path = config_path or Path("nuguard.yaml")
    if not resolved_config_path.exists():
        console.print(
            f"[red]Error:[/red] nuguard.yaml not found at {resolved_config_path}. "
            "This command writes discovered config into that file, so it must already exist "
            "(run 'nuguard init' first if you don't have one)."
        )
        raise typer.Exit(code=1)

    try:
        cfg = load_config(resolved_config_path)
        raw = yaml.safe_load(resolved_config_path.read_text(encoding="utf-8")) or {}
        raw = _expand_env_vars(raw)
    except (ConfigError, yaml.YAMLError) as exc:
        console.print(f"[red]Error:[/red] failed to load config: {exc}")
        raise typer.Exit(code=1) from exc

    target_block = raw.get("target") if isinstance(raw, dict) else None
    browser_cfg = BrowserDiscoveryConfig.from_target_block(target_block)

    target_url = target_override or cfg.target_url
    if not target_url:
        console.print("[red]Error:[/red] No target URL. Set target.url in nuguard.yaml or pass --target.")
        raise typer.Exit(code=1)

    auth = cfg.resolved_auth_config()
    effective_cookie_file = cookie_file_override or (resolved_config_path.parent / "cookies.txt")

    console.print("\n[bold]NuGuard Browser Login Discovery[/bold]")
    console.print(f"  Target:  {target_url}")
    console.print(f"  Mode:    {'headed' if not headless else 'headless'}")
    console.print()

    try:
        result = await discover_browser(
            BrowserDiscoveryRequest(
                target_url=target_url,
                auth_type="basic",
                auth_username=auth.username,
                auth_password=auth.password,
                headless=headless,
                timeout_s=timeout_s,
                sniff_chat=sniff_chat,
                chat_message=chat_message,
                browser_discovery=browser_cfg,
            ),
            cookie_file=effective_cookie_file,
        )
    except BrowserLoginError as exc:
        _print_browser_login_error(exc)
        raise typer.Exit(code=_STEP_EXIT_CODES.get(exc.step, 1)) from exc
    except Exception as exc:  # noqa: BLE001 — never leak a raw traceback for this command
        console.print(f"[red]✗ Unexpected error during browser login:[/red] {exc}")
        _log.exception("discover-browser: unexpected error")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]✓[/green] Session captured: {result.cookies_written_to}")
    if result.identity_payload is not None:
        console.print(f"  Identity probe ({result.identity_url}): {result.identity_payload}")
    if result.sniffed_chat_request is not None:
        console.print(f"  Sniffed chat request ({result.sniffed_endpoint}): {result.sniffed_chat_request}")
    for field_name, value in result.candidate_extra_fields.items():
        console.print(f"  [green]Confirmed extra field[/green]: {field_name} = {value!r}")
    for field_name, values in result.ambiguous_fields.items():
        console.print(f"  [yellow]Unconfirmed candidate field[/yellow]: {field_name} = {values!r}")
    for warning in result.warnings:
        console.print(f"  [yellow]Warning:[/yellow] {warning}")

    # Cookie file path written into nuguard.yaml relative to the config file's
    # own directory, matching existing convention, so the read-side rebasing
    # in nuguard/config.py::_rebase_relative_paths resolves it correctly
    # regardless of the caller's cwd.
    try:
        relative_cookie_path = "./" + str(
            effective_cookie_file.resolve().relative_to(resolved_config_path.resolve().parent)
        )
    except ValueError:
        relative_cookie_path = str(effective_cookie_file)

    try:
        editable = load_editable_yaml(resolved_config_path)
        before_text = dump_to_string(editable)
        summary = apply_target_updates(
            editable,
            cookie_file=relative_cookie_path,
            chat_payload_extras=result.candidate_extra_fields,
            ambiguous_extras=result.ambiguous_fields,
        )
        after_text = dump_to_string(editable)
    except BrowserLoginError as exc:
        _print_browser_login_error(exc)
        raise typer.Exit(code=_STEP_EXIT_CODES.get(exc.step, 1)) from exc

    console.print("\n[bold]Proposed nuguard.yaml changes[/bold]")
    for line in summary:
        console.print(f"  • {line}")
    console.print()
    console.print(render_diff(before_text, after_text, filename=str(resolved_config_path)))

    if not write:
        console.print("\n[dim]Dry run — nuguard.yaml not modified. Re-run with --write to apply.[/dim]")
        raise typer.Exit(code=0)

    if not yes:
        confirmed = typer.confirm("Apply these changes to nuguard.yaml?")
        if not confirmed:
            console.print("[yellow]Aborted — nuguard.yaml not modified.[/yellow]")
            raise typer.Exit(code=1)

    try:
        write_yaml(editable)
    except BrowserLoginError as exc:
        _print_browser_login_error(exc)
        console.print(
            f"[yellow]Your captured cookies are still saved at {result.cookies_written_to} — "
            "you can set target.auth manually.[/yellow]"
        )
        raise typer.Exit(code=_STEP_EXIT_CODES.get(exc.step, 1)) from exc

    console.print(f"[green]✓ nuguard.yaml updated.[/green] Re-run 'nuguard target verify --config {resolved_config_path}' to confirm.")
    raise typer.Exit(code=0)


def _print_browser_login_error(exc: BrowserLoginError) -> None:
    console.print(f"[red]✗ {exc}[/red]")
    if exc.step == "playwright_not_installed":
        console.print('  [dim]Run: pip install "nuguard\\[browser]"[/dim]')
    elif exc.step == "browser_binary_missing":
        console.print("  [dim]Run: playwright install chromium[/dim]")
