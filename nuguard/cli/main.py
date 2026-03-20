"""NuGuard CLI entry point.

Registered as the ``nuguard`` console script via ``pyproject.toml``.
"""

from __future__ import annotations

import typer

from nuguard.cli.commands.sbom import sbom_app
from nuguard.cli.commands.analyze import analyze_app
from nuguard.cli.commands.policy import policy_app
from nuguard.cli.commands.redteam import redteam_app
from nuguard.cli.commands.seed import seed_app
from nuguard.cli.commands.report import report_app
from nuguard.cli.commands.findings import findings_app
from nuguard.cli.commands.replay import replay_app

app = typer.Typer(
    name="nuguard",
    help="NuGuard AI Security CLI — SBOM generation, static analysis, policy compliance, and red-team testing.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(sbom_app, name="sbom")
app.add_typer(analyze_app, name="analyze")
app.add_typer(policy_app, name="policy")
app.add_typer(redteam_app, name="redteam")
app.add_typer(seed_app, name="seed")
app.add_typer(report_app, name="report")
app.add_typer(findings_app, name="findings")
app.add_typer(replay_app, name="replay")


if __name__ == "__main__":
    app()
