"""``nuguard sbom`` sub-commands: generate (default), validate, register, show."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

import typer
from rich.console import Console
from rich.table import Table

from nuguard.common.errors import SbomError, ValidationError
from nuguard.common.logging import get_logger
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.generator import SbomGenerator
from nuguard.sbom.validator import validate_sbom

_log = get_logger(__name__)
_console = Console()
_err_console = Console(stderr=True, style="bold red")

sbom_app = typer.Typer(
    help="SBOM generation, validation, and management.",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Shared option definitions (reused by both the callback and generate command)
# ---------------------------------------------------------------------------
_OPT_SOURCE = typer.Option(
    None, "--source", "-s",
    help="Path to application source directory.",
    exists=False, file_okay=False, dir_okay=True, resolve_path=True,
)
_OPT_FROM_REPO = typer.Option(
    None, "--from-repo",
    help="Git repository URL to clone and scan (e.g. https://github.com/org/repo).",
)
_OPT_REF = typer.Option(
    "main", "--ref",
    help="Branch, tag, or commit to check out when using --from-repo.",
)
_OPT_TOKEN = typer.Option(
    None, "--token",
    help="GitHub personal access token for private repos (falls back to GH_TOKEN / GITHUB_TOKEN).",
)
_OPT_OUTPUT = typer.Option(
    Path("app.sbom.json"), "--output", "-o",
    help="Output path for the generated SBOM JSON.",
)
_OPT_LLM = typer.Option(
    False, "--llm/--no-llm", help="Enable LLM enrichment (requires LITELLM_API_KEY).",
)
_OPT_FORMAT = typer.Option(
    "json", "--format", "-f",
    help="Additional output format: json (default, SBOM only) | cyclonedx | markdown.",
)
_OPT_CONFIG = typer.Option(
    None, "--config", help="Path to nuguard.yaml (default: ./nuguard.yaml).", exists=False,
)


def _inject_token(url: str, token: str) -> str:
    """Embed *token* into an HTTPS git URL for private-repo authentication."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    netloc = f"{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _resolve_token(token: str | None) -> str | None:
    """Return the first non-empty token from flag → GH_TOKEN → GITHUB_TOKEN."""
    return token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _do_generate(
    source: Optional[Path],
    from_repo: Optional[str],
    ref: str,
    token: Optional[str],
    output: Path,
    llm: bool,
    format: str,
    config_file: Optional[Path],
) -> None:
    """Core generate logic shared by the callback and the explicit subcommand."""
    # Fall back to nuguard.yaml's source field when --source is not provided
    if source is None and from_repo is None:
        from nuguard.config import load_config  # noqa: PLC0415
        cfg = load_config(config_file)
        if cfg.source_path:
            source = Path(cfg.source_path)

    if source is None and from_repo is None:
        _err_console.print(
            "Provide --source <dir> or --from-repo <url> (or set source: in nuguard.yaml)."
        )
        raise typer.Exit(code=1)

    config = AiSbomConfig(enable_llm=llm)
    gen = SbomGenerator(config=config)

    try:
        if from_repo:
            resolved_token = _resolve_token(token)
            clone_url = _inject_token(from_repo, resolved_token) if resolved_token else from_repo
            _console.print(f"[bold]Cloning[/bold] {from_repo} ({ref}) …")
            # Pass the original URL as source_ref to avoid leaking the token
            from nuguard.sbom.extractor.core import AiSbomExtractor  # noqa: PLC0415
            extractor = AiSbomExtractor()
            doc = extractor.extract_from_repo(
                clone_url, ref=ref, config=config, source_ref=from_repo
            )
        else:
            assert source is not None  # guarded by the exit above
            if not source.exists():
                _err_console.print(f"Directory not found: {source}")
                raise typer.Exit(code=1)
            _console.print(f"[bold]Scanning[/bold] {source} …")
            doc = gen.from_path(source, output=None)
    except SbomError as exc:
        _err_console.print(f"Error: {exc}")
        raise typer.Exit(code=3) from exc

    from nuguard.sbom.extractor.serializer import AiSbomSerializer  # noqa: PLC0415

    # JSON SBOM is always written — it is the primary artifact
    output.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    _console.print(
        f"[green]SBOM written to[/green] {output} "
        f"([bold]{len(doc.nodes)}[/bold] nodes, [bold]{len(doc.edges)}[/bold] edges)"
    )

    # Additional format output (alongside the JSON SBOM)
    stem = output.stem.removesuffix(".sbom") if output.stem.endswith(".sbom") else output.stem
    if format == "cyclonedx":
        cdx_path = output.with_name(f"{stem}.cdx.json")
        data = AiSbomSerializer.to_cyclonedx(doc)
        cdx_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _console.print(f"[green]CycloneDX written to[/green] {cdx_path}")
    elif format == "markdown":
        from nuguard.sbom.toolbox.plugins.markdown_exporter import (
            MarkdownExporterPlugin,  # noqa: PLC0415
        )
        md_path = output.with_name(f"{stem}.sbom.md")
        sbom_dict = json.loads(AiSbomSerializer.to_json(doc))
        result = MarkdownExporterPlugin().run(sbom_dict, {})
        md_path.write_text(result.details.get("markdown", ""), encoding="utf-8")
        _console.print(f"[green]Markdown report written to[/green] {md_path}")

    if doc.summary and doc.summary.node_counts:
        table = Table(title="Node Summary", show_header=True)
        table.add_column("Type")
        table.add_column("Count", justify="right")
        for node_type, count in sorted(doc.summary.node_counts.items()):
            table.add_row(node_type, str(count))
        _console.print(table)


@sbom_app.callback(invoke_without_command=True)
def sbom_default(
    ctx: typer.Context,
    source: Optional[Path] = _OPT_SOURCE,
    from_repo: Optional[str] = _OPT_FROM_REPO,
    ref: str = _OPT_REF,
    token: Optional[str] = _OPT_TOKEN,
    output: Path = _OPT_OUTPUT,
    llm: bool = _OPT_LLM,
    format: str = _OPT_FORMAT,
    config_file: Optional[Path] = _OPT_CONFIG,
) -> None:
    """Generate an AI-SBOM (default) or run a sub-command.

    When called without a sub-command, behaves identically to ``nuguard sbom generate``.
    """
    if ctx.invoked_subcommand is not None:
        return
    _do_generate(source, from_repo, ref, token, output, llm, format, config_file)


@sbom_app.command("generate")
def generate(
    source: Optional[Path] = _OPT_SOURCE,
    from_repo: Optional[str] = _OPT_FROM_REPO,
    ref: str = _OPT_REF,
    token: Optional[str] = _OPT_TOKEN,
    output: Path = _OPT_OUTPUT,
    llm: bool = _OPT_LLM,
    format: str = _OPT_FORMAT,
    config_file: Optional[Path] = _OPT_CONFIG,
) -> None:
    """Generate an AI-SBOM by scanning SOURCE or cloning --from-repo."""
    _do_generate(source, from_repo, ref, token, output, llm, format, config_file)


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
    from nuguard.db.local import LocalDb
    from nuguard.sbom.extractor.serializer import AiSbomSerializer

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

    typer.echo(AiSbomSerializer.to_json(doc))


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
        nuguard sbom plugin run markdown --sbom app.sbom.json --format markdown
        nuguard sbom plugin run sarif --sbom app.sbom.json
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
            md = result.details.get("markdown", "")
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
