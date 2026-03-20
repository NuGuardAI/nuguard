"""``nuguard analyze`` command stub.

TODO: Implement static SBOM analysis via nuguard.analysis.static_analyzer.
"""

from __future__ import annotations

import typer

analyze_app = typer.Typer(
    help="Static risk analysis from the AI-SBOM (no running app required).",
    no_args_is_help=True,
)


@analyze_app.callback(invoke_without_command=True)
def analyze(
    ctx: typer.Context,
    sbom: str = typer.Option(None, "--sbom", help="Path to AI-SBOM JSON file."),
    format: str = typer.Option("markdown", "--format", "-f", help="Output format: markdown | sarif | json."),
    policy: str = typer.Option(None, "--policy", help="Path to Cognitive Policy Markdown file."),
    min_severity: str = typer.Option("medium", "--min-severity", help="Minimum severity: critical | high | medium | low."),
) -> None:
    """Run static analysis against the AI-SBOM.

    TODO: Implement full static analysis pipeline.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo("nuguard analyze: not yet implemented.")
    raise typer.Exit(code=3)
