"""``nuguard findings`` command stub.

TODO: Implement findings listing via nuguard.db.local.
"""

from __future__ import annotations

import typer

findings_app = typer.Typer(
    help="List findings from a completed red-team test run.",
    no_args_is_help=True,
)


@findings_app.callback(invoke_without_command=True)
def findings(
    ctx: typer.Context,
    test_id: str = typer.Option(..., "--test-id", help="ID of the completed test run."),
    severity: str = typer.Option(None, "--severity", help="Comma-separated severity levels to filter by."),
) -> None:
    """List findings for TEST_ID.

    TODO: Implement findings retrieval and display.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo("nuguard findings: not yet implemented.")
    raise typer.Exit(code=3)
