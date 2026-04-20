"""nuguard behavior — intent-aware AI application behavior analysis."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

behavior_app = typer.Typer(
    name="behavior",
    help="Analyze AI application behavior against its declared intent.",
)

_console = Console()
_err_console = Console(stderr=True)
_log = logging.getLogger(__name__)


@behavior_app.callback(invoke_without_command=True)
def behavior_command(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to nuguard.yaml"
    ),
    mode: str = typer.Option(
        "static+dynamic",
        "--mode",
        "-m",
        help="Analysis mode: static | dynamic | static+dynamic",
    ),
    target: Optional[str] = typer.Option(
        None, "--target", help="Override behavior.target URL"
    ),
    policy: Optional[Path] = typer.Option(
        None, "--policy", help="Path to Cognitive Policy Markdown"
    ),
    intent: Optional[str] = typer.Option(
        None, "--intent", help="Override app intent (one-line description)"
    ),
    canary: Optional[Path] = typer.Option(
        None, "--canary", help="Path to canary.json seed file"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write report to this path"
    ),
    format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text | json | markdown"
    ),
    fail_on: str = typer.Option(
        "high",
        "--fail-on",
        help="Exit non-zero when any finding meets this severity: critical | high | medium | low",
    ),
    baseline: Optional[Path] = typer.Option(
        None, "--baseline", help="Path to a previous BehaviorAnalysisResult JSON for regression detection"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print detailed turn traces."
    ),
    static: bool = typer.Option(
        False, "--static", help="Run static analysis only (shorthand for --mode static)"
    ),
    dynamic: bool = typer.Option(
        False, "--dynamic", help="Run dynamic analysis only (shorthand for --mode dynamic)"
    ),
) -> None:
    """Analyze AI application behavior against its declared intent.

    Runs static SBOM-policy alignment checks and/or dynamic behavior testing
    with intent-aware per-turn judging.

    \\b
    Examples:
      nuguard behavior
      nuguard behavior --static --config ./nuguard.yaml
      nuguard behavior --dynamic --target http://localhost:8090
      nuguard behavior --policy ./policy.md --output ./behavior-report.md --format markdown
      nuguard behavior --mode static+dynamic --fail-on critical
    """
    if ctx.invoked_subcommand is not None:
        return

    # Shorthand flags override --mode
    effective_mode = mode
    if static and not dynamic:
        effective_mode = "static"
    elif dynamic and not static:
        effective_mode = "dynamic"

    asyncio.run(
        _run_behavior(
            config_path=config,
            mode=effective_mode,
            target_override=target,
            policy_path=policy,
            intent_text=intent,
            canary_path=canary,
            output_path=output,
            fmt=format,
            fail_on=fail_on,
            baseline_path=baseline,
            verbose=verbose,
        )
    )


async def _run_behavior(
    config_path: Optional[Path],
    mode: str,
    target_override: Optional[str],
    policy_path: Optional[Path],
    intent_text: Optional[str],
    canary_path: Optional[Path],
    output_path: Optional[Path],
    fmt: str,
    fail_on: str,
    baseline_path: Optional[Path],
    verbose: bool,
) -> None:
    """Internal async implementation of the behavior command."""
    from nuguard.behavior.analyzer import BehaviorAnalyzer
    from nuguard.behavior.report import to_json, to_markdown, to_text
    from nuguard.cli.report_meta import ReportMeta
    from nuguard.common.llm_client import LLMClient
    from nuguard.config import load_config

    # 1. Load config
    try:
        cfg = load_config(config_path)
    except Exception as exc:
        _err_console.print(f"[red]Error:[/red] Failed to load config: {exc}")
        raise typer.Exit(code=1)

    bc = cfg.behavior_config

    # 2. Apply CLI overrides
    updates: dict = {}
    if target_override:
        updates["target"] = target_override
    if verbose:
        updates["verbose"] = True
    if canary_path:
        updates["canary"] = str(canary_path)
    if updates:
        bc = bc.model_copy(update=updates)

    # 3. Check that target is set for dynamic mode
    if "dynamic" in mode and not bc.target:
        _err_console.print(
            "[red]Error:[/red] behavior.target is not set. "
            "Add it to nuguard.yaml or pass --target."
        )
        raise typer.Exit(code=1)

    # 4. Load AI-SBOM and auto-enrich if confidence is low
    sbom = None
    sbom_path_obj: Path | None = None
    raw_sbom_path = cfg.sbom_path
    if raw_sbom_path:
        sbom_path_obj = Path(raw_sbom_path)
        try:
            from nuguard.sbom.serializer import AiSbomSerializer
            sbom = AiSbomSerializer.from_json(sbom_path_obj.read_text())
            _console.print(f"[dim]Loaded SBOM: {raw_sbom_path}[/dim]")
        except Exception as exc:
            _log.warning("Could not load SBOM from %s: %s", raw_sbom_path, exc)

    # Auto-enrich low-confidence SBOMs before scenario generation (mirrors redteam).
    _model = cfg.litellm_model or ""
    _api_base = cfg.litellm_api_base if _model.startswith("azure") else None

    if sbom is not None:
        from nuguard.cli.common import enrich_sbom_for_run

        sbom = await enrich_sbom_for_run(
            sbom=sbom,
            sbom_path=sbom_path_obj,
            target_url=bc.target or None,
            llm_enabled=bc.use_llm,
            llm_model=cfg.litellm_model,
            llm_api_key=cfg.litellm_api_key,
            llm_api_base=_api_base,
            probe_auth_header=bc.auth.header if bc.auth.type != "none" else None,
            log_prefix="behavior",
        )

    # 5. Load policy
    resolved_policy_path = policy_path or (Path(cfg.policy_path) if cfg.policy_path else None)
    policy_obj = None
    controls = None
    if resolved_policy_path and resolved_policy_path.exists():
        try:
            from nuguard.policy.parser import parse_policy
            policy_obj = parse_policy(resolved_policy_path.read_text())
            _console.print(f"[dim]Loaded policy: {resolved_policy_path}[/dim]")
        except Exception as exc:
            _log.warning("Could not parse policy from %s: %s", resolved_policy_path, exc)

    # 6. Build LLM client
    llm_client = None
    if bc.use_llm or cfg.litellm_api_key:
        try:
            llm_client = LLMClient(
                model=cfg.litellm_model,
                api_key=cfg.litellm_api_key,
                api_base=_api_base,
            )
        except Exception as exc:
            _log.warning("Could not build LLM client: %s", exc)

    # Compile controls from policy — after LLM client so it can be passed in
    if policy_obj is not None:
        try:
            from nuguard.policy.compiler import compile_controls
            policy_text = resolved_policy_path.read_text() if resolved_policy_path else ""
            controls = await compile_controls(policy_text, use_llm=bc.use_llm, llm_client=llm_client)
        except Exception as exc:
            _log.debug("Could not compile controls: %s", exc)

    # 7. Run analysis
    _console.print(f"[bold]Running behavior analysis[/bold]  mode={mode}")
    try:
        analyzer = BehaviorAnalyzer(
            config=bc,
            sbom=sbom,
            policy=policy_obj,
            controls=controls,
            llm_client=llm_client,
        )
        result = await analyzer.analyze(mode=mode)
    except Exception as exc:
        from nuguard.common.errors import AuthError, TargetUnavailableError  # noqa: PLC0415
        if isinstance(exc, TargetUnavailableError):
            _err_console.print(
                f"[red]Error:[/red] target is unreachable at {exc.url!r}.\n"
                "Ensure the application is running and the URL is correct.\n"
                "Run 'nuguard target verify' to diagnose connectivity."
            )
        elif isinstance(exc, AuthError):
            _err_console.print(
                f"[red]Error:[/red] authentication failed — {exc}\n"
                "Check your auth credentials in nuguard.yaml or --auth-header.\n"
                "Run 'nuguard target verify' to diagnose authentication."
            )
        else:
            _err_console.print(f"[red]Error:[/red] Behavior analysis failed: {exc}")
            _log.exception("Behavior analysis failed")
        raise typer.Exit(code=2)

    # 8. Output report
    meta = ReportMeta(
        target_url=str(getattr(bc, "target", "") or ""),
    )

    report_text: str | None = None
    if fmt == "json":
        report_text = to_json(result, meta)
    elif fmt == "markdown":
        report_text = to_markdown(result, meta)
    else:
        to_text(result, meta)

    if output_path is not None:
        if report_text is None:
            report_text = to_markdown(result, meta)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_text, encoding="utf-8")
        _console.print(f"[green]Report written to:[/green] {output_path}")
    elif report_text is not None:
        _console.print(report_text)

    # 9. Exit code based on fail_on severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    fail_threshold = severity_order.get(fail_on.lower(), 1)
    all_findings = list(result.static_findings) + list(result.dynamic_findings)
    worst = min(
        (severity_order.get(str(f.get("severity", "info")).lower(), 4) for f in all_findings),
        default=99,
    )
    if worst <= fail_threshold:
        raise typer.Exit(code=1)
