"""``nuguard replay`` command stub.

TODO: Implement deterministic scan replay via nuguard.redteam.executor.
"""

from __future__ import annotations

import typer

replay_app = typer.Typer(
    help="Deterministically replay a completed red-team test run.",
    no_args_is_help=True,
)


@replay_app.callback(invoke_without_command=True)
def replay(
    ctx: typer.Context,
    test_id: str = typer.Option(..., "--test-id", help="ID of the test run to replay."),
    target: str = typer.Option(None, "--target", help="Override the target URL for replay."),
) -> None:
    """Replay TEST_ID deterministically against TARGET.

    .. note::

        Placeholder. A run-history store plus signed trace replay (see
        issue #161) is required before a previously-run scan can be
        byte-for-byte replayed. Until then, re-run the original
        ``nuguard redteam`` / ``nuguard behavior`` invocation with the
        same ``nuguard.yaml`` to obtain a comparable result.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo(
        "nuguard replay: not yet implemented. "
        "Issue #161 tracks adding a run-history store with signed trace replay, "
        "which this command depends on. Workaround: re-run the original "
        "`nuguard redteam` or `nuguard behavior` command with the same nuguard.yaml.",
        err=True,
    )
    raise typer.Exit(code=3)
