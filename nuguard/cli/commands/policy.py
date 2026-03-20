"""``nuguard policy`` sub-commands stub.

TODO: Implement policy validate, check, and show commands.
"""

from __future__ import annotations

import typer

policy_app = typer.Typer(
    help="Cognitive policy validation and compliance framework checking.",
    no_args_is_help=True,
)


@policy_app.command("validate")
def validate(
    file: str = typer.Option(..., "--file", "-f", help="Path to Cognitive Policy Markdown file."),
) -> None:
    """Lint the cognitive policy for completeness and ambiguity.

    TODO: Implement policy linting via nuguard.policy.validator.
    """
    typer.echo("nuguard policy validate: not yet implemented.")
    raise typer.Exit(code=3)


@policy_app.command("check")
def check(
    sbom: str = typer.Option(..., "--sbom", help="Path to AI-SBOM JSON file."),
    policy: str = typer.Option(..., "--policy", help="Path to Cognitive Policy Markdown file."),
) -> None:
    """Cross-check the policy against the SBOM for structural gaps.

    TODO: Implement policy ↔ SBOM static check via nuguard.policy.checker.
    """
    typer.echo("nuguard policy check: not yet implemented.")
    raise typer.Exit(code=3)


@policy_app.command("show")
def show(
    policy_id: str = typer.Option(..., "--policy-id", help="ID of the registered policy."),
) -> None:
    """Display a registered policy's parsed sections.

    TODO: Implement policy storage and retrieval via nuguard.db.local.
    """
    typer.echo("nuguard policy show: not yet implemented.")
    raise typer.Exit(code=3)
