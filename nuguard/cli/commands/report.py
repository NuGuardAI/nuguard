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

    .. note::

        Placeholder. A run-history store (see issue #161) is required before
        reports from past ``--test-id`` runs can be regenerated. Until then,
        use ``nuguard redteam --output <path>.<fmt>`` (or ``--format
        markdown|sarif|json``) to write the report inline.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo(
        "nuguard report: not yet implemented. "
        "Issue #161 tracks adding a run-history store, which this command depends on. "
        "Workaround: run `nuguard redteam --output report.<fmt> --format <fmt>` directly.",
        err=True,
    )
    raise typer.Exit(code=3)
