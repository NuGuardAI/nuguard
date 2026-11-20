"""``nuguard serve``: run the remote scan API; ``nuguard serve keygen``: create an API key."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

serve_app = typer.Typer(
    # "\[" stops rich from treating [service] as a markup tag.
    help="Run the remote scan API (requires: pip install \"nuguard\\[service]\").",
    invoke_without_command=True,
)


@serve_app.callback()
def serve(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="nuguard.yaml containing a service: section."
    ),
    host: Optional[str] = typer.Option(None, "--host", help="Override service.bind."),
    port: Optional[int] = typer.Option(None, "--port", help="Override service.port."),
) -> None:
    """Start the HTTP job API (SBOM generation + static analysis of git repositories)."""
    if ctx.invoked_subcommand is not None:
        return
    from nuguard.service.__main__ import run  # noqa: PLC0415

    raise typer.Exit(code=run(config, host, port))


@serve_app.command("keygen")
def keygen(
    key_id: str = typer.Option("default", "--id", help="Identifier recorded as the caller id."),
) -> None:
    """Print a new API key once, with the SHA-256 to put in service.api_keys."""
    from nuguard.service.auth import generate_key, hash_key  # noqa: PLC0415

    key = generate_key()
    typer.echo(f"API key (shown once, give it to the caller): {key}")
    typer.echo("Add to nuguard.yaml:")
    typer.echo("service:\n  api_keys:")
    typer.echo(f"    - id: {key_id}\n      sha256: {hash_key(key)}")
