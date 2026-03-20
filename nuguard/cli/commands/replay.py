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

    TODO: Implement signed trace replay logic.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo("nuguard replay: not yet implemented.")
    raise typer.Exit(code=3)
