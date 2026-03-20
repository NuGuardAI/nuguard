"""Xelo CLI — AI SBOM generator.

Commands
--------
xelo scan <PATH|URL>
    Extract AI components from a local directory or a remote git repo.
    Target is treated as a URL when it contains ``://``.

    --format json          Xelo-native JSON (default)
    --format cyclonedx     Package dependencies only as CycloneDX 1.6 (AI SBOM details not included)
    --format cyclonedx-ext Standard deps BOM + AI-BOM merged (CycloneDX 1.6, extended with AI components)
    --format spdx          AI components as SPDX 3.0.1 JSON-LD (optional: pip install xelo[spdx])
    --output <file>        Write to file (default: stdout)
    --llm                  Enable LLM enrichment for this run
    --ref <branch>         Branch/ref to clone when target is a URL
    --token <token>        Git token for private repo access (or GH_TOKEN / GITHUB_TOKEN env)
    --plugin <name>        Run a toolbox plugin inline after scanning (no intermediate file needed)
    --plugin-output <file> Plugin output file (default: stdout)
    --plugin-config k=v    Plugin config entry, repeatable (same as --config in plugin run)

xelo validate <FILE>
    Validate a Xelo-native JSON document against the schema.

xelo schema [--output <file>]
    Emit the Xelo JSON schema.

xelo plugin list
    List all available toolbox plugins.

xelo plugin run <PLUGIN> <SBOM> [--output <file>] [--config key=value ...]
    Run a named toolbox plugin against an existing SBOM JSON file.

    Available plugins: vulnerability, atlas, license, dependency,
                       sarif, cyclonedx, spdx, markdown, ghas, aws-security-hub, xray

Logging
-------
  --verbose   INFO-level logs to stderr (scan progress, file counts, fallbacks)
  --debug     DEBUG-level logs + full tracebacks on errors
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from .config import AiSbomConfig
from .extractor import AiSbomExtractor
from .models import AiSbomDocument
from .serializer import AiSbomSerializer

_log = logging.getLogger("xelo")


def _setup_logging(verbose: bool, debug: bool) -> None:
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    logging.root.setLevel(level)
    logging.root.addHandler(handler)


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE pairs from .env into process environment.

    Existing environment variables are not overridden.
    """
    if not path.exists() or not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _build_extraction_config(args: argparse.Namespace) -> AiSbomConfig:
    config = AiSbomConfig()
    if getattr(args, "llm", False):
        config.enable_llm = True
    if getattr(args, "llm_model", None) is not None:
        config.llm_model = args.llm_model
    if getattr(args, "llm_budget_tokens", None) is not None:
        config.llm_budget_tokens = args.llm_budget_tokens
    if getattr(args, "llm_api_key", None) is not None:
        config.llm_api_key = args.llm_api_key
    if getattr(args, "llm_api_base", None) is not None:
        config.llm_api_base = args.llm_api_base
    return config


def _die(msg: str, args: argparse.Namespace | None = None) -> None:
    """Print an error and exit 1.  Show traceback only with --debug."""
    debug = getattr(args, "debug", False)
    if debug:
        traceback.print_exc(file=sys.stderr)
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    """Attach LLM flags to a sub-parser."""
    p.add_argument("--llm", action="store_true", help="Enable LLM enrichment for this run.")
    p.add_argument(
        "--llm-model", metavar="<model>", help="LLM model string (overrides XELO_LLM_MODEL)."
    )
    p.add_argument(
        "--llm-budget-tokens",
        type=int,
        metavar="<n>",
        help="Token budget (overrides XELO_LLM_BUDGET_TOKENS).",
    )
    p.add_argument(
        "--llm-api-key", metavar="<key>", help="LLM API key (overrides XELO_LLM_API_KEY)."
    )
    p.add_argument(
        "--llm-api-base", metavar="<url>", help="LLM base URL (overrides XELO_LLM_API_BASE)."
    )


# ── Toolbox plugin registry ───────────────────────────────────────────────────

# (module_path, class_name, network_required, short_description)
_PLUGIN_REGISTRY: dict[str, tuple[str, str, str, str]] = {
    "vulnerability": (
        "xelo.toolbox.plugins.vulnerability",
        "VulnerabilityScannerPlugin",
        "No",
        "Structural VLA rules — missing guardrails, over-privileged agents",
    ),
    "atlas": (
        "xelo.toolbox.plugins.atlas_annotator",
        "AtlasAnnotatorPlugin",
        "No",
        "Map findings to MITRE ATLAS v2 techniques and mitigations",
    ),
    "license": (
        "xelo.toolbox.plugins.license_checker",
        "LicenseCheckerPlugin",
        "No",
        "Check dependency licence compliance",
    ),
    "dependency": (
        "xelo.toolbox.plugins.dependency",
        "DependencyAnalyzerPlugin",
        "No",
        "Dependency breakdown and freshness analysis",
    ),
    "sarif": (
        "xelo.toolbox.plugins.sarif_exporter",
        "SarifExporterPlugin",
        "No",
        "Export findings as SARIF 2.1.0 (GitHub Code Scanning compatible)",
    ),
    "cyclonedx": (
        "xelo.toolbox.plugins.cyclonedx_exporter",
        "CycloneDxExporter",
        "No",
        "Export nodes as CycloneDX 1.6 BOM",
    ),
    "spdx": (
        "xelo.toolbox.plugins.spdx_exporter",
        "SpdxExporter",
        "No",
        "Export nodes as SPDX 3.0.1 JSON-LD (install xelo[spdx] for spdx-tools validation)",
    ),
    "markdown": (
        "xelo.toolbox.plugins.markdown_exporter",
        "MarkdownExporterPlugin",
        "No",
        "Generate a human-readable Markdown report",
    ),
    "ghas": (
        "xelo.toolbox.plugins.ghas_uploader",
        "GhasUploaderPlugin",
        "Yes",
        "Upload SARIF to GitHub Advanced Security (--config token=<ghp_…> github_repo=owner/repo ref=refs/heads/main commit_sha=<sha>)",
    ),
    "aws-security-hub": (
        "xelo.toolbox.plugins.aws_security_hub",
        "AwsSecurityHubPlugin",
        "Yes",
        "Push findings to AWS Security Hub (--config region=<r> aws_account_id=<id>)",
    ),
    "xray": (
        "xelo.toolbox.plugins.xray",
        "XrayPlugin",
        "Yes",
        "Submit SBOM to JFrog Xray (--config url=<u> project=<p> token=<t> tenant_id=<tid> application_id=<aid>)",
    ),
}

# Plugins whose ToolResult.details IS the output dict (serialised as JSON when emitting).
# For these plugins the full details dict becomes the file content.
_PLUGIN_DICT_OUTPUT: frozenset[str] = frozenset({"sarif", "cyclonedx", "spdx"})

# For content-exporter plugins that store a raw string inside ToolResult.details.
_PLUGIN_CONTENT_KEY: dict[str, str] = {
    "markdown": "markdown",  # str — Markdown text
    "atlas": "markdown",  # str — Markdown text (only when --config format=markdown)
    "vulnerability": "markdown",  # str — Markdown text (only when --config format=markdown)
}


def _load_plugin(name: str) -> Any:
    """Import and return an instantiated toolbox plugin by short name."""
    if name not in _PLUGIN_REGISTRY:
        valid = ", ".join(sorted(_PLUGIN_REGISTRY))
        raise ValueError(
            f"unknown plugin {name!r}. Available: {valid}.\n"
            "Run 'xelo plugin list' for descriptions."
        )
    module_path, class_name, _, _ = _PLUGIN_REGISTRY[name]
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(
            f"plugin {name!r} requires optional dependencies that are not installed: {exc}"
        ) from exc
    cls = getattr(mod, class_name)
    return cls()


def _parse_config_pairs(pairs: list[str]) -> dict[str, Any]:
    """Parse ['key=value', ...] into a dict, auto-casting booleans and numbers."""
    result: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--config entry must be key=value, got: {pair!r}")
        key, val = pair.split("=", 1)
        key = key.strip()
        val = val.strip()
        if val.lower() in ("true", "yes", "1"):
            result[key] = True
        elif val.lower() in ("false", "no", "0"):
            result[key] = False
        else:
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = val
    return result


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="xelo", description="Deterministic AI SBOM generator")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable INFO-level logging to stderr"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable DEBUG-level logging and full tracebacks"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── scan ──────────────────────────────────────────────────────────────
    scan_p = subparsers.add_parser("scan", help="Scan a local directory or remote git repo")
    scan_p.add_argument(
        "target", metavar="<path|url>", help="Local path or git URL (detected by '://')"
    )
    scan_p.add_argument(
        "--ref",
        default="main",
        metavar="<branch>",
        help="Branch/ref when target is a URL (default: main)",
    )
    scan_p.add_argument(
        "--format",
        choices=["json", "cyclonedx", "cyclonedx-ext", "spdx"],
        default="json",
        help="Output format: json (default), cyclonedx, cyclonedx-ext, spdx",
    )
    scan_p.add_argument(
        "--output",
        default=None,
        metavar="<file>",
        help=(
            "Output file for the SBOM (default: stdout, or /dev/null when --plugin is used "
            "and no output path is specified)"
        ),
    )
    scan_p.add_argument(
        "--token",
        metavar="<token>",
        default=None,
        help=("Git auth token for private repos. Falls back to GH_TOKEN / GITHUB_TOKEN env vars."),
    )
    _add_llm_args(scan_p)
    scan_p.add_argument(
        "--plugin",
        metavar="<name>",
        dest="plugin",
        default=None,
        help="Run a toolbox plugin inline after scanning (no intermediate file needed).",
    )
    scan_p.add_argument(
        "--plugin-output",
        default="-",
        metavar="<file>",
        help="Plugin output file (default: stdout); only meaningful with --plugin.",
    )
    scan_p.add_argument(
        "--plugin-config",
        action="append",
        metavar="key=value",
        default=[],
        help="Plugin config entry (repeatable); only meaningful with --plugin.",
    )

    # ── validate ──────────────────────────────────────────────────────────
    validate_p = subparsers.add_parser("validate", help="Validate a Xelo-native JSON document")
    validate_p.add_argument("input", metavar="<file>")

    # ── schema ────────────────────────────────────────────────────────────
    schema_p = subparsers.add_parser("schema", help="Emit the Xelo JSON schema")
    schema_p.add_argument(
        "--output", default="-", metavar="<file>", help="File to write schema to (default: stdout)"
    )

    # ── plugin ────────────────────────────────────────────────────────────
    plugin_p = subparsers.add_parser("plugin", help="List or run toolbox plugins")
    plugin_sp = plugin_p.add_subparsers(dest="plugin_command", required=True)

    plugin_sp.add_parser("list", help="List available toolbox plugins")

    run_p = plugin_sp.add_parser(
        "run", help="Run a toolbox plugin against an existing SBOM JSON file"
    )
    run_p.add_argument(
        "plugin_name",
        metavar="<plugin>",
        help="Plugin name — see 'xelo plugin list'",
    )
    run_p.add_argument(
        "sbom_file",
        metavar="<sbom>",
        help="Path to a Xelo-native JSON SBOM file",
    )
    run_p.add_argument(
        "--output",
        default="-",
        metavar="<file>",
        help="Write plugin output to file (default: stdout)",
    )
    run_p.add_argument(
        "--config",
        action="append",
        metavar="key=value",
        default=[],
        help="Plugin config entry (repeatable, e.g. --config region=us-east-1)",
    )
    run_p.add_argument(
        "--config-file",
        metavar="<json>",
        help=(
            "JSON file containing plugin config (merged with --config; --config takes precedence)"
        ),
    )

    args = parser.parse_args()
    _setup_logging(args.verbose, args.debug)

    command_map = {
        "scan": _handle_scan,
        "validate": _handle_validate,
        "schema": _handle_schema,
        "plugin": _handle_plugin,
    }
    handler = command_map.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    handler(args)


# ── plugin ────────────────────────────────────────────────────────────────────


def _handle_plugin(args: argparse.Namespace) -> None:
    sub = getattr(args, "plugin_command", None)
    if sub == "list":
        _handle_plugin_list(args)
    elif sub == "run":
        _handle_plugin_run(args)
    else:
        print("Usage: xelo plugin <list|run>", file=sys.stderr)
        sys.exit(1)


def _handle_plugin_list(_args: argparse.Namespace) -> None:
    name_w = max(len(n) for n in _PLUGIN_REGISTRY) + 2
    print("Available toolbox plugins:\n")
    print(f"  {'Name':<{name_w}}  {'Network':<8}  Description")
    print(f"  {'-' * name_w}  {'-------':<8}  -----------")
    for name, (_mod, _cls, network, desc) in _PLUGIN_REGISTRY.items():
        print(f"  {name:<{name_w}}  {network:<8}  {desc}")
    print()
    print("Run 'xelo plugin run <name> <sbom> --help' for per-plugin flags.")


def _handle_plugin_run(args: argparse.Namespace) -> None:
    plugin_name: str = args.plugin_name
    sbom_path = Path(args.sbom_file)
    output: str = args.output

    # ── Load SBOM ─────────────────────────────────────────────────────────
    if not sbom_path.exists():
        _die(f"file not found: {sbom_path}", args)
        return
    try:
        sbom: dict[str, Any] = json.loads(sbom_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(f"invalid JSON in {sbom_path}: {exc}", args)
        return

    # ── Build config dict ──────────────────────────────────────────────────
    plugin_config: dict[str, Any] = {}
    if getattr(args, "config_file", None):
        cf = Path(args.config_file)
        if not cf.exists():
            _die(f"config file not found: {cf}", args)
            return
        try:
            plugin_config = json.loads(cf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _die(f"invalid JSON in config file: {exc}", args)
            return
    if getattr(args, "config", None):
        try:
            plugin_config.update(_parse_config_pairs(args.config))
        except ValueError as exc:
            _die(str(exc), args)
            return

    # ── Load and run plugin ────────────────────────────────────────────────
    try:
        plugin = _load_plugin(plugin_name)
    except (ValueError, RuntimeError) as exc:
        _die(str(exc), args)
        return

    _log.info("running plugin '%s' on %s", plugin_name, sbom_path)
    try:
        result = plugin.run(sbom, plugin_config)
    except Exception as exc:  # noqa: BLE001
        _die(f"plugin '{plugin_name}' failed: {exc}", args)
        return

    # ── Emit output ────────────────────────────────────────────────────────
    if plugin_name in _PLUGIN_DICT_OUTPUT:
        # sarif / cyclonedx: ToolResult.details IS the BOM/SARIF dict.
        _emit(json.dumps(result.details, indent=2), output, args)
    elif plugin_name in _PLUGIN_CONTENT_KEY:
        # Exporter plugins: write the raw content string directly.
        # Fall back to full JSON when the content key is absent (e.g. atlas
        # without --config format=markdown).
        content_key = _PLUGIN_CONTENT_KEY[plugin_name]
        raw = result.details.get(content_key, "")
        if raw:
            _emit(raw, output, args)
        else:
            _emit(json.dumps(result.model_dump(), indent=2), output, args)
    else:
        # Analysis / upload plugins: emit full ToolResult as JSON.
        _emit(json.dumps(result.model_dump(), indent=2), output, args)

    if output != "-":
        print(f"{result.status}: {result.message} → {output}")
    else:
        _log.info("plugin '%s' complete — %s", plugin_name, result.message)


# ── scan ──────────────────────────────────────────────────────────────────────


def _inject_token(url: str, token: str) -> str:
    """Embed a token into an HTTPS git URL for private-repo authentication."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    # Replace or set the userinfo portion: https://TOKEN@host/...
    netloc = f"{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _resolve_token(args: argparse.Namespace) -> str | None:
    """Return the git token from --token flag or environment variables."""
    token: str | None = getattr(args, "token", None)
    if token:
        return token
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _handle_scan(args: argparse.Namespace) -> None:
    extractor = AiSbomExtractor()
    config = _build_extraction_config(args)
    target: str = args.target

    try:
        if "://" in target:
            token = _resolve_token(args)
            clone_url = _inject_token(target, token) if token else target
            _log.info("cloning %s @ %s", target, args.ref)
            doc = extractor.extract_from_repo(
                clone_url,
                ref=args.ref,
                config=config,
                source_ref=target,
            )
            local_root = Path(".")
        else:
            local_root = Path(target).resolve()
            if not local_root.exists():
                _die(f"path not found: {local_root}", args)
            if not local_root.is_dir():
                _die(f"not a directory: {local_root}", args)
            _log.info("scanning %s", local_root)
            doc = extractor.extract_from_path(local_root, config=config)
    except RuntimeError as exc:
        _die(str(exc), args)
        return

    _log.info("extraction complete: %d nodes, %d edges", len(doc.nodes), len(doc.edges))
    sbom_output: str = args.output or (os.devnull if getattr(args, "plugin", None) else "-")
    _write_output(args, doc, local_root, sbom_output)

    if getattr(args, "plugin", None):
        _run_inline_plugin(args, doc)


def _run_inline_plugin(args: argparse.Namespace, doc: AiSbomDocument) -> None:
    """Run a toolbox plugin against the just-scanned doc (no temp file required)."""
    plugin_name: str = args.plugin
    plugin_output: str = args.plugin_output

    plugin_config: dict[str, Any] = {}
    if getattr(args, "plugin_config", None):
        try:
            plugin_config = _parse_config_pairs(args.plugin_config)
        except ValueError as exc:
            _die(str(exc), args)
            return

    try:
        plugin = _load_plugin(plugin_name)
    except (ValueError, RuntimeError) as exc:
        _die(str(exc), args)
        return

    sbom = json.loads(AiSbomSerializer.to_json(doc))
    _log.info("running plugin '%s' inline", plugin_name)
    try:
        result = plugin.run(sbom, plugin_config)
    except Exception as exc:  # noqa: BLE001
        _die(f"plugin '{plugin_name}' failed: {exc}", args)
        return

    if plugin_name in _PLUGIN_DICT_OUTPUT:
        _emit(json.dumps(result.details, indent=2), plugin_output, args)
    elif plugin_name in _PLUGIN_CONTENT_KEY:
        content_key = _PLUGIN_CONTENT_KEY[plugin_name]
        _emit(result.details.get(content_key, ""), plugin_output, args)
    else:
        _emit(json.dumps(result.model_dump(), indent=2), plugin_output, args)

    if plugin_output != "-":
        print(f"{result.status}: {result.message} → {plugin_output}")
    else:
        _log.info("plugin '%s' complete — %s", plugin_name, result.message)


def _write_output(
    args: argparse.Namespace,
    doc: AiSbomDocument,
    root: Path,
    output: str,
) -> None:
    fmt: str = args.format
    try:
        if fmt == "json":
            content = AiSbomSerializer.to_json(doc)
        elif fmt == "cyclonedx":
            content = AiSbomSerializer.dump_cyclonedx_json(doc)
        elif fmt == "spdx":
            content = _build_spdx(doc)
        else:  # cyclonedx-ext
            content = _build_cyclonedx_ext(args, root, doc)
    except (PermissionError, OSError) as exc:
        _die(f"I/O error: {exc}", args)
        return

    _emit(content, output, args)
    if output != "-":
        _log.info("done — %s written", output)
        print(f"{len(doc.nodes)} nodes, {len(doc.edges)} edges → {output}")
    else:
        _log.info("done — %d nodes, %d edges", len(doc.nodes), len(doc.edges))


def _build_cyclonedx_ext(args: argparse.Namespace, root: Path, ai_doc: AiSbomDocument) -> str:
    from .cdx_tools import CycloneDxGenerator
    from .merger import AiBomMerger

    gen = CycloneDxGenerator()
    std_bom, method = gen.generate(root)
    merger = AiBomMerger()
    cyclonedx_ext = merger.merge(std_bom, ai_doc, generator_method=method)
    return json.dumps(cyclonedx_ext, indent=2)


def _build_spdx(doc: AiSbomDocument) -> str:
    from .toolbox.plugins.spdx_exporter import _to_spdx3

    payload = _to_spdx3(doc)
    return json.dumps(payload, indent=2)


def _emit(content: str, output: str, args: argparse.Namespace) -> None:
    if output == "-":
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
    else:
        try:
            Path(output).write_text(content, encoding="utf-8")
        except OSError as exc:
            _die(f"cannot write {output}: {exc}", args)


# ── validate ──────────────────────────────────────────────────────────────────


def _handle_validate(args: argparse.Namespace) -> None:
    in_path = Path(args.input)
    _log.info("validating %s", in_path)
    try:
        raw = in_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _die(f"file not found: {in_path}", args)
        return
    except OSError as exc:
        _die(f"cannot read file: {exc}", args)
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(f"not valid JSON: {exc}", args)
        return
    try:
        AiSbomDocument.model_validate(data)
    except Exception as exc:
        _die(f"validation failed: {exc}", args)
        return
    print("OK — document is valid")


# ── schema ────────────────────────────────────────────────────────────────────


def _handle_schema(args: argparse.Namespace) -> None:
    schema = AiSbomDocument.model_json_schema()
    content = json.dumps(schema, indent=2)
    output: str = args.output
    _emit(content, output, args)
    if output != "-":
        print(f"schema written → {output}")


if __name__ == "__main__":
    main()
