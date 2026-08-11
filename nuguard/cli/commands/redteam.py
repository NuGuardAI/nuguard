"""nuguard redteam — dynamic adversarial testing against a live AI application.

The execution engine (scenario generation, executor, orchestrator, LLM
evaluation, reporting) was removed for a ground-up rewrite; only the
scenario catalog (``nuguard.redteam.catalog``) survives. ``catalog-export``
still works. The scan entry point is a stub until the new engine lands.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

_NOT_IMPLEMENTED = (
    "Error: the redteam execution engine is being rebuilt and is not yet "
    "available. The scenario catalog still works — try "
    "'nuguard redteam catalog-export'."
)

redteam_app = typer.Typer(
    name="redteam",
    help="Dynamic adversarial testing against a live AI application.",
    no_args_is_help=True,
)


@redteam_app.callback(invoke_without_command=True)
def redteam(ctx: typer.Context) -> None:
    """Run dynamic red-team testing against a live AI application.

    Not yet implemented — the engine is being rebuilt from scratch.
    """
    if ctx.invoked_subcommand is not None:
        return
    typer.echo(_NOT_IMPLEMENTED, err=True)
    raise typer.Exit(code=1)


@redteam_app.command(name="catalog-export")
def catalog_export(
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the catalog YAML to this path. Prints to stdout when omitted.",
    ),
) -> None:
    """Export the built-in scenario catalog to YAML for customization.

    Common customizations:
      - Set enabled: false to skip specific scenarios
      - Adjust base_impact (0-10) to change selection priority under --profile ci/standard
      - Modify expected_control or success_signal descriptions
      - Add new entries with a unique id and a registered builder_key
    """
    from nuguard.redteam.catalog.loader import export_catalog_yaml
    from nuguard.redteam.catalog.registry import SCENARIO_CATALOG

    yaml_text = export_catalog_yaml(SCENARIO_CATALOG, path=output)
    if output:
        typer.echo(
            f"Catalog exported: {len(SCENARIO_CATALOG)} scenarios → {output}\n"
            f"Edit the file and run: nuguard redteam --catalog {output} ..."
        )
    else:
        typer.echo(yaml_text, nl=False)
