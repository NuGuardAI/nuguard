"""``nuguard init`` command.

Creates starter project files for first-time NuGuard setup.
"""

from __future__ import annotations

from pathlib import Path

import typer

init_app = typer.Typer(
    help="Create starter NuGuard project files.",
    no_args_is_help=False,
)

_COGNITIVE_POLICY_TEMPLATE = """# Cognitive Policy

## Allowed Topics

## Restricted Topics

## Restricted Actions

## HITL Triggers

## Data Classification

## Rate Limits
"""


@init_app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    path: Path = typer.Option(
        Path("cognitive_policy.md"),
        "--path",
        help="Path for the starter cognitive policy Markdown file.",
    ),
    force: bool = typer.Option(
        False,
        "--force/--no-force",
        help="Overwrite the file if it already exists.",
    ),
) -> None:
    """Create a starter ``cognitive_policy.md`` with section headers only."""
    if ctx.invoked_subcommand is not None:
        return

    if path.exists() and not force:
        typer.echo(
            f"Error: {path} already exists. Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(code=1)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_COGNITIVE_POLICY_TEMPLATE, encoding="utf-8")
    typer.echo(f"Created starter cognitive policy at {path}")
