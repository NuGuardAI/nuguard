"""``nuguard report`` command stub.

TODO: Implement report generation via nuguard.output.
"""

from __future__ import annotations

import typer

report_app = typer.Typer(
    help="Generate reports for completed red-team test runs.",
    no_args_is_help=True,
)


@report_app.callback(invoke_without_command=True)
def report(
    ctx: typer.Context,
    test_id: str = typer.Option(..., "--test-id", help="ID of the completed test run."),
    format: str = typer.Option("markdown", "--format", "-f", help="Output format: markdown | sarif | json."),
) -> None:
    """Generate a report for TEST_ID.

    TODO: Implement report generation.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo("nuguard report: not yet implemented.")
    raise typer.Exit(code=3)
