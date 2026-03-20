"""``nuguard sbom`` sub-commands: generate, validate, register, show."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from nuguard.common.errors import SbomError, ValidationError
from nuguard.common.logging import get_logger
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.generator import SbomGenerator
from nuguard.sbom.parser import parse_sbom
from nuguard.sbom.validator import validate_sbom

_log = get_logger(__name__)
_console = Console()
_err_console = Console(stderr=True, style="bold red")

sbom_app = typer.Typer(
    help="SBOM generation, validation, and management.",
    no_args_is_help=True,
)


@sbom_app.command("generate")
def generate(
    source: Path = typer.Option(
        ...,
        "--source",
        "-s",
        help="Path to application source directory.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    output: Path = typer.Option(
        Path("app.sbom.json"),
        "--output",
        "-o",
        help="Output path for the generated SBOM JSON.",
    ),
    llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Enable LLM enrichment (requires LITELLM_API_KEY).",
    ),
    format: str = typer.Option(
        "json",
        "--format",
        "-f",
        help="Output format: json | cyclonedx.",
    ),
) -> None:
    """Generate an AI-SBOM by scanning SOURCE."""
    config = AiSbomConfig(enable_llm=llm)
    gen = SbomGenerator(config=config)

    _console.print(f"[bold]Scanning[/bold] {source} …")
    try:
        doc = gen.from_path(source, output=None)
    except SbomError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=3) from exc

    if format == "cyclonedx":
        from nuguard.sbom.extractor.serializer import AiSbomSerializer

        data = AiSbomSerializer.to_cyclonedx(doc)
        output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        from nuguard.sbom.extractor.serializer import AiSbomSerializer

        output.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")

    _console.print(
        f"[green]SBOM written to[/green] {output} "
        f"([bold]{len(doc.nodes)}[/bold] nodes, [bold]{len(doc.edges)}[/bold] edges)"
    )

    # Summary table
    if doc.summary.node_counts:
        table = Table(title="Node Summary", show_header=True)
        table.add_column("Type")
        table.add_column("Count", justify="right")
        for node_type, count in sorted(doc.summary.node_counts.items()):
            table.add_row(node_type, str(count))
        _console.print(table)


@sbom_app.command("validate")
def validate(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Path to the SBOM JSON file to validate.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Validate FILE against the bundled AI-SBOM JSON Schema."""
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        validate_sbom(raw)
    except ValidationError as exc:
        _err_console.print(f"Validation failed: {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        _err_console.print(f"Error reading file: {exc}")
        raise typer.Exit(code=3) from exc

    _console.print(f"[green]✓ {file.name} is valid.[/green]")


@sbom_app.command("register")
def register(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Path to the SBOM JSON file to register.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Register FILE in the local database (~/.nuguard/nuguard.db)."""
    from nuguard.sbom.extractor.serializer import AiSbomSerializer
    from nuguard.db.local import LocalDb

    try:
        raw = file.read_text(encoding="utf-8")
        doc = AiSbomSerializer.from_json(raw)
    except Exception as exc:
        _err_console.print(f"Error reading SBOM: {exc}")
        raise typer.Exit(code=3) from exc

    try:
        db = LocalDb()
        sbom_id = db.save_sbom(doc)
    except Exception as exc:
        _err_console.print(f"Error saving SBOM: {exc}")
        raise typer.Exit(code=3) from exc

    _console.print(f"[green]SBOM registered.[/green] ID: [bold]{sbom_id}[/bold]")


@sbom_app.command("show")
def show(
    sbom_id: str = typer.Option(
        ...,
        "--sbom-id",
        help="ID of the registered SBOM to display.",
    ),
) -> None:
    """Display the registered SBOM with SBOM_ID."""
    from nuguard.db.local import LocalDb
    from nuguard.sbom.extractor.serializer import AiSbomSerializer

    db = LocalDb()
    doc = db.get_sbom(sbom_id)
    if doc is None:
        _err_console.print(f"SBOM '{sbom_id}' not found.")
        raise typer.Exit(code=1)

    _console.print(AiSbomSerializer.to_json(doc))


@sbom_app.command("schema")
def schema() -> None:
    """Print the bundled aibom.schema.json to stdout."""
    from nuguard.sbom.schema import get_schema_path

    schema_path = get_schema_path()
    if schema_path.exists():
        import sys
        sys.stdout.write(schema_path.read_text(encoding="utf-8"))
    else:
        _err_console.print(f"Schema file not found at {schema_path}")
        raise typer.Exit(code=1)


@sbom_app.command("plugin")
def plugin_cmd(
    action: str = typer.Argument(help="Action: 'run' or 'list'"),
    plugin_name: Optional[str] = typer.Argument(None, help="Plugin name (for 'run' action)"),
    sbom_file: Optional[Path] = typer.Option(
        None,
        "--sbom",
        help="Path to the SBOM JSON file.",
    ),
    format: str = typer.Option(
        "json",
        "--format",
        "-f",
        help="Output format: json | markdown",
    ),
) -> None:
    """Run a toolbox plugin or list available plugins.

    Examples:
        nuguard sbom plugin list
        nuguard sbom plugin run vulnerability --sbom app.sbom.json
        nuguard sbom plugin run markdown --sbom app.sbom.json --format markdown
    """
    from nuguard.sbom.toolbox.orchestrator import PluginOrchestrator

    orchestrator = PluginOrchestrator()

    if action == "list":
        _console.print("[bold]Available plugins:[/bold]")
        for name in orchestrator.list_plugins():
            _console.print(f"  - {name}")
        return

    if action == "run":
        if not plugin_name:
            _err_console.print("Plugin name required for 'run' action.")
            raise typer.Exit(code=1)
        if not sbom_file:
            _err_console.print("--sbom FILE required for 'run' action.")
            raise typer.Exit(code=1)
        if not sbom_file.exists():
            _err_console.print(f"SBOM file not found: {sbom_file}")
            raise typer.Exit(code=1)

        from nuguard.sbom.extractor.serializer import AiSbomSerializer

        try:
            raw = sbom_file.read_text(encoding="utf-8")
            doc = AiSbomSerializer.from_json(raw)
        except Exception as exc:
            _err_console.print(f"Error reading SBOM: {exc}")
            raise typer.Exit(code=3) from exc

        try:
            result = orchestrator.run(plugin_name, doc)
        except ValueError as exc:
            _err_console.print(str(exc))
            raise typer.Exit(code=1) from exc

        if format == "markdown" and result.details:
            # For markdown plugin, print the markdown content
            md = result.details[0].get("markdown", "")
            if md:
                _console.print(md)
                return

        # Default: print as JSON
        import json as _json
        output = {
            "status": result.status,
            "message": result.message,
            "details": result.details,
        }
        _console.print(_json.dumps(output, indent=2, default=str))
        return

    _err_console.print(f"Unknown action '{action}'. Use 'run' or 'list'.")
    raise typer.Exit(code=1)
