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

    .. note::

        Placeholder. A run-history store (see issue #161) is required before
        findings from past ``--test-id`` runs can be looked up. Until then,
        use ``nuguard redteam --output <path>.json`` to write the latest
        findings to a file and inspect that file directly.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo(
        "nuguard findings: not yet implemented. "
        "Issue #161 tracks adding a run-history store, which this command depends on. "
        "Workaround: run `nuguard redteam --output findings.json` and inspect the JSON file.",
        err=True,
    )
    raise typer.Exit(code=3)
