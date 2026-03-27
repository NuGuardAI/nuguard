"""nuguard validate — happy-path and policy compliance runner (Phase 3)."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

validate_app = typer.Typer(name="validate", help="Validate AI application behaviour (Phase 3).")
console = typer.get_text_stream("stdout")


@validate_app.callback(invoke_without_command=True)
def validate_command(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to nuguard.yaml"),
    ] = None,
) -> None:
    """Validate AI application happy-path behaviour and cognitive policy compliance.

    Run capability probes, happy-path simulations, boundary assertions, and
    per-turn policy evaluations against the declared target.

    \b
    Phase 3 feature — not yet implemented.
    Use 'nuguard target verify' to check connectivity and auth now.
    """
    if ctx.invoked_subcommand is None:
        typer.echo(
            "nuguard validate is planned for Phase 3.\n"
            "Run 'nuguard target verify' to verify connectivity and authentication."
        )
        raise typer.Exit(code=0)
