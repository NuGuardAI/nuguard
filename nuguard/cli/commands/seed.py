"""``nuguard seed`` command stub.

TODO: Implement canary data seeding via nuguard.redteam.target.canary.
"""

from __future__ import annotations

import typer

seed_app = typer.Typer(
    help="Seed canary data into the target application before red-team testing.",
    no_args_is_help=True,
)


@seed_app.callback(invoke_without_command=True)
def seed(
    ctx: typer.Context,
    target: str = typer.Option(None, "--target", help="URL of the running AI application."),
    seed_file: str = typer.Option(None, "--seed-file", help="Path to canary seed JSON file."),
    output_canary: str = typer.Option(None, "--output-canary", help="Write canary result to this path."),
) -> None:
    """Seed canary data into TARGET before running red-team tests.

    TODO: Implement canary seeding logic.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo("nuguard seed: not yet implemented.")
    raise typer.Exit(code=3)
