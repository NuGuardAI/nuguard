"""``nuguard redteam`` command stub.

TODO: Implement dynamic red-team testing via nuguard.redteam.executor.
"""

from __future__ import annotations

import typer

redteam_app = typer.Typer(
    help="Dynamic adversarial testing against the running AI application.",
    no_args_is_help=True,
)


@redteam_app.callback(invoke_without_command=True)
def redteam(
    ctx: typer.Context,
    sbom: str = typer.Option(None, "--sbom", help="Path to AI-SBOM JSON file."),
    policy: str = typer.Option(None, "--policy", help="Path to Cognitive Policy Markdown file."),
    target: str = typer.Option(None, "--target", help="URL of the running AI application."),
    canary: str = typer.Option(None, "--canary", help="Path to canary JSON file."),
    profile: str = typer.Option("ci", "--profile", help="Scan profile: ci | full."),
    scenarios: str = typer.Option(None, "--scenarios", help="Comma-separated scenario types."),
    min_impact_score: float = typer.Option(0.0, "--min-impact-score", help="Minimum impact score to report."),
) -> None:
    """Run a red-team test against the target AI application.

    TODO: Implement full red-team pipeline.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo("nuguard redteam: not yet implemented.")
    raise typer.Exit(code=3)
