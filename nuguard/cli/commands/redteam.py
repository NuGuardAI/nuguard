"""nuguard redteam — dynamic adversarial testing against a live AI application."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig
    from nuguard.common.llm_client import LLMClient
    from nuguard.config import RedteamFindingTriggers, RedteamV2Settings
    from nuguard.models.token_usage import TokenUsage

import typer

from nuguard.cli.common import output_path_for_format, parse_output_formats
from nuguard.cli.report_meta import ReportMeta
from nuguard.common.logging import get_logger

_log = get_logger(__name__)

redteam_app = typer.Typer(
    name="redteam",
    help="Dynamic adversarial testing against a live AI application.",
    no_args_is_help=True,
)


@redteam_app.callback(invoke_without_command=True)
def redteam(
    ctx: typer.Context,
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to nuguard.yaml config file."
    ),
    sbom: Optional[Path] = typer.Option(
        None, "--sbom", help="Path to AI-SBOM JSON file."
    ),
    source: Optional[Path] = typer.Option(
        None,
        "--source",
        help=(
            "Source directory of the app.  Used to read .env files and auto-launch "
            "the app locally when --target is omitted."
        ),
    ),
    policy: Optional[Path] = typer.Option(
        None, "--policy", help="Path to Cognitive Policy Markdown file."
    ),
    target: Optional[str] = typer.Option(
        None,
        "--target",
        help=(
            "URL of the running AI application.  When omitted, NuGuard tries to "
            "use URLs discovered in the SBOM (local → staging → production).  "
            "Pass --launch to start the app automatically."
        ),
    ),
    launch: bool = typer.Option(
        False,
        "--launch/--no-launch",
        help=(
            "Auto-start the app using the startup command discovered in the SBOM, "
            "then stop it when the scan finishes.  Requires --source."
        ),
    ),
    canary: Optional[Path] = typer.Option(
        None, "--canary", help="Path to canary JSON file."
    ),
    catalog: Optional[Path] = typer.Option(
        None,
        "--catalog",
        help=(
            "Path to a custom scenario catalog YAML file. Replaces the built-in catalog. "
            "Generate a starting file with: nuguard redteam catalog-export"
        ),
    ),
    profile: str = typer.Option(
        "ci", "--profile", help="Scan profile: ci (fast, safe) or full."
    ),
    engine: Optional[str] = typer.Option(
        None,
        "--engine",
        help=(
            "Red-team engine: v1 (default, stable) or v2 (knowledge-base-driven, "
            "phased, layered evaluation; in development)."
        ),
    ),
    scenarios: Optional[str] = typer.Option(
        None, "--scenarios", help="Comma-separated scenario types to run (default: all)."
    ),
    min_impact_score: float = typer.Option(
        0.0,
        "--min-impact-score",
        help="Minimum pre-impact score [0-10] for scenario inclusion.",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write findings JSON to this path."
    ),
    format: list[str] | None = typer.Option(
        None,
        "--format",
        "-f",
        help=(
            "Output format(s): text | json | markdown | sarif. "
            "Repeat --format or pass comma-separated values."
        ),
    ),
    fail_on: str = typer.Option(
        "high",
        "--fail-on",
        help="Exit non-zero if any finding meets this severity: critical|high|medium|low.",
    ),
    guided: Optional[bool] = typer.Option(
        None,
        "--guided/--no-guided",
        help=(
            "Enable adaptive multi-turn guided conversations (requires redteam LLM). "
            "Default: on when a redteam LLM is configured."
        ),
    ),
    guided_max_turns: Optional[int] = typer.Option(
        None, "--guided-max-turns", help="Max turns per guided conversation (default: 12)."
    ),
    guided_concurrency: Optional[int] = typer.Option(
        None, "--guided-concurrency", help="Max parallel guided conversations (default: 3)."
    ),
    verbose: Optional[bool] = typer.Option(
        None,
        "--verbose/--no-verbose",
        "-v/-V",
        help="Print detailed turn traces.  Overrides verbose setting in nuguard.yaml.",
    ),
) -> None:
    """Run dynamic red-team testing against a live AI application.

    When --target is omitted, NuGuard reads the SBOM for URLs discovered
    during the scan (local dev, staging, production) and picks the best one.
    Use --launch to have NuGuard start the app locally before testing.
    """
    if ctx.invoked_subcommand is not None:
        return

    # Resolve from nuguard.yaml if not provided on CLI
    from nuguard.config import load_config

    cfg = load_config(config_path)
    from nuguard.remediation.llm import resolve_remediation_llm_client

    remediation_llm_client = resolve_remediation_llm_client(cfg)
    sbom_path = sbom or (Path(cfg.sbom_path) if cfg.sbom_path else None)
    policy_path = policy or (Path(cfg.policy_path) if cfg.policy_path else None)
    target_url = target or cfg.target_url
    canary_path = canary or (Path(cfg.canary_path) if cfg.canary_path else None)
    catalog_path = catalog or (
        Path(cfg.redteam_catalog_path) if cfg.redteam_catalog_path else None
    )
    _source_path_val = getattr(cfg, "source_path", None)
    source_dir = source or (Path(str(_source_path_val)) if _source_path_val else None)
    # CLI flag takes precedence; fall back to config default
    effective_engine = (engine or cfg.redteam_engine or "v1").lower()
    if effective_engine not in ("v1", "v2"):
        typer.echo(
            f"Error: invalid --engine {effective_engine!r}; expected 'v1' or 'v2'", err=True
        )
        raise typer.Exit(code=1)
    effective_profile = profile if profile != "ci" else cfg.redteam_profile
    effective_min_impact = (
        min_impact_score if min_impact_score != 0.0 else cfg.min_impact_score
    )
    raw_formats = format if format else [cfg.output_format or "text"]
    try:
        effective_formats = parse_output_formats(
            raw_formats,
            default_format="text",
            allowed_formats={"text", "json", "markdown", "sarif"},
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if len(effective_formats) > 1 and output is None:
        typer.echo(
            "Error: --output is required when multiple --format values are requested",
            err=True,
        )
        raise typer.Exit(code=1)
    effective_fail_on = fail_on if fail_on != "high" else cfg.fail_on
    effective_scenarios = (
        [s.strip() for s in scenarios.split(",")] if scenarios
        else cfg.redteam_scenarios or []
    )
    effective_guided = guided if guided is not None else cfg.redteam_guided_conversations
    effective_guided_max_turns = guided_max_turns if guided_max_turns is not None else cfg.redteam_guided_max_turns
    effective_guided_concurrency = guided_concurrency if guided_concurrency is not None else cfg.redteam_guided_concurrency
    effective_guided_mutation_mode = cfg.redteam_guided_mutation_mode
    effective_verbose = verbose if verbose is not None else cfg.redteam_verbose
    finding_triggers = cfg.resolved_redteam_finding_triggers()
    if not finding_triggers.any_enabled():
        typer.echo(
            "Warning: all redteam finding triggers are disabled; scans may produce empty findings by design."
        )

    if effective_scenarios:
        from nuguard.redteam.executor.orchestrator import validate_scenario_filter  # noqa: PLC0415

        unrecognized_scenarios = validate_scenario_filter(effective_scenarios)
        if unrecognized_scenarios:
            typer.echo(
                f"Warning: redteam.scenarios contains unrecognized value(s) "
                f"{unrecognized_scenarios} — these won't reliably match any scenario "
                "and may silently drop coverage. Valid values: prompt-driven-threat, "
                "policy-violation, data-exfiltration, privilege-escalation, tool-abuse, "
                "mcp-toxic-flow, api-attack, agentic-trust-abuse, recon-inference."
            )

    # Load custom catalog if provided
    custom_catalog = None
    if catalog_path:
        import yaml as _yaml  # noqa: PLC0415

        from nuguard.redteam.catalog.loader import load_catalog_yaml  # noqa: PLC0415

        try:
            custom_catalog = load_catalog_yaml(catalog_path)
            typer.echo(
                f"  Custom catalog: {len(custom_catalog)} scenarios from {catalog_path}"
            )
        except FileNotFoundError:
            typer.echo(f"Error: catalog file not found: {catalog_path}", err=True)
            raise typer.Exit(code=1)
        except _yaml.YAMLError as exc:
            typer.echo(f"Error: malformed catalog YAML in {catalog_path}: {exc}", err=True)
            raise typer.Exit(code=1)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)

    # Validate SBOM
    if not sbom_path or not sbom_path.exists():
        typer.echo(
            "Error: --sbom is required (or set sbom: in nuguard.yaml)", err=True
        )
        raise typer.Exit(code=1)

    # Load SBOM early so we can read discovered URLs
    from nuguard.sbom.serializer import AiSbomSerializer

    try:
        sbom_doc = AiSbomSerializer.from_json(sbom_path.read_text())
    except Exception as exc:
        typer.echo(f"Error loading SBOM: {exc}", err=True)
        raise typer.Exit(code=1)

    # Re-run topology enrichment in case the SBOM file predates it (idempotent).
    from nuguard.sbom.enricher import enrich as _enrich_topology  # noqa: PLC0415

    _enrich_topology(sbom_doc)

    # Resolve target URL — explicit > SBOM discovery
    if not target_url:
        target_url = _resolve_target_url(sbom_doc, launch=launch)

    if not target_url and not launch:
        typer.echo(
            "Error: no target URL available.  Provide --target, or use --launch to "
            "start the app from the SBOM startup command.",
            err=True,
        )
        raise typer.Exit(code=1)

    if effective_engine == "v2":
        typer.echo("  Engine: v2 (scaffold — see redteam-v2 implementation plan)")
        runner = _run_redteam_v2(
            sbom_doc=sbom_doc,
            sbom_path=sbom_path,
            policy_path=policy_path,
            target_url=target_url,
            canary_path=canary_path,
            settings=cfg.resolved_redteam_v2_settings(),
            profile=effective_profile,
            chat_path=cfg.target_endpoint or "",
            auth_config=cfg.resolved_auth_config(),
            chat_payload_extras=cfg.redteam_chat_payload_extras or None,
            redteam_llm_model=cfg.redteam_llm_model,
            redteam_llm_api_key=cfg.redteam_llm_api_key,
            redteam_llm_api_base=cfg.redteam_llm_api_base,
            eval_llm_model=cfg.redteam_eval_llm_model or cfg.litellm_model or None,
            eval_llm_api_key=cfg.redteam_eval_llm_api_key or cfg.litellm_api_key or None,
            eval_llm_api_base=cfg.redteam_eval_llm_api_base,
            verbose=effective_verbose,
        )
    else:
        runner = _run_redteam(
            sbom_doc=sbom_doc,
            sbom_path=sbom_path,
            policy_path=policy_path,
            target_url=target_url,
            canary_path=canary_path,
            profile=effective_profile,
            min_impact_score=effective_min_impact,
            scenario_filter=effective_scenarios,
            auth_config=cfg.resolved_auth_config(),
            headers_override=cfg.redteam_headers,
            source_dir=source_dir,
            launch=launch,
            chat_path=cfg.target_endpoint,
            chat_payload_key=cfg.redteam_chat_payload_key,
            chat_payload_list=cfg.redteam_chat_payload_list,
            chat_response_key=cfg.redteam_chat_response_key or None,
            chat_payload_extras=cfg.redteam_chat_payload_extras or None,
            guided_conversations=effective_guided,
            guided_max_turns=effective_guided_max_turns,
            guided_concurrency=effective_guided_concurrency,
            guided_mutation_mode=effective_guided_mutation_mode,
            tree_breadth=cfg.redteam_tree_breadth,
            tree_max_depth=cfg.redteam_tree_max_depth,
            strict_outcome=cfg.redteam_strict_outcome,
            credentials=cfg.redteam_credentials or None,
            redteam_llm_model=cfg.redteam_llm_model,
            redteam_llm_api_key=cfg.redteam_llm_api_key,
            redteam_llm_api_base=cfg.redteam_llm_api_base,
            eval_llm_model=cfg.redteam_eval_llm_model or cfg.litellm_model or None,
            eval_llm_api_key=cfg.redteam_eval_llm_api_key or cfg.litellm_api_key or None,
            eval_llm_api_base=cfg.redteam_eval_llm_api_base,
            remediation_llm_client=remediation_llm_client,
            prompt_cache_dir=cfg.redteam_prompt_cache_dir,
            # ^ eval_llm falls back to top-level llm.model/api_key when redteam.eval_llm is not set
            finding_triggers=finding_triggers,
            verbose=effective_verbose,
            scenario_timeout=cfg.redteam_scenario_timeout,
            concurrency=cfg.redteam_concurrency,
            turn_delay_seconds=cfg.redteam_turn_delay_seconds,
            scenario_delay_seconds=cfg.redteam_scenario_delay_seconds,
            similar_miss_threshold=cfg.redteam_similar_miss_threshold,
            hard_refusal_abort_turns=cfg.redteam_hard_refusal_abort_turns,
            stall_abort_threshold=cfg.redteam_stall_abort_threshold,
            skip_discovery=cfg.redteam_skip_discovery,
            discovery_max_turns=cfg.redteam_discovery_max_turns,
            catalog=custom_catalog,
            pre_run_warmup=cfg.redteam_pre_run_warmup,
            verify_findings=cfg.redteam_verify_findings,
            golden_data=cfg.redteam_golden_data or None,
            suppress_spa_html_auth_bypass=cfg.redteam_suppress_spa_html_auth_bypass,
            codegen_escalation_enabled=cfg.redteam_codegen_escalation_enabled,
        )

    try:
        (
            findings,
            scenario_records,
            scan_outcome,
            config_notes,
            catalog_coverage,
            input_tokens_used,
            output_tokens_used,
            coverage_tracker,
            token_usage,
            resolved_chat_path,
            resolved_chat_path_source,
            remediation_plan,
        ) = asyncio.run(runner)  # type: ignore[misc]
    except Exception as exc:
        from nuguard.common.errors import AuthError, TargetUnavailableError  # noqa: PLC0415
        if isinstance(exc, TargetUnavailableError):
            typer.echo(
                f"Error: target is unreachable at {exc.url!r}.\n"
                "Ensure the application is running and the URL is correct.\n"
                "Run 'nuguard target verify' to diagnose connectivity.",
                err=True,
            )
        elif isinstance(exc, AuthError):
            typer.echo(
                f"Error: authentication failed — {exc}\n"
                "Check your auth credentials in nuguard.yaml or --auth-header.\n"
                "Run 'nuguard target verify' to diagnose authentication.",
                err=True,
            )
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Output
    llm_models = [m for m in [cfg.redteam_llm_model, cfg.redteam_eval_llm_model] if m]
    meta = ReportMeta(
        llm_models=llm_models,
        verbose=effective_verbose,
        target_url=target_url or "",
        target_endpoint=cfg.target_endpoint or "/chat",
        effective_endpoint=resolved_chat_path,
        target_endpoint_source=resolved_chat_path_source,
        finding_triggers=finding_triggers.model_dump(),
        scan_profile=effective_profile,
    )
    # remediation_plan is already synthesized inside _run_orchestrator (v1) via
    # nuguard.redteam.public_api.run_redteam(), which consults the SBOM to
    # produce concrete system-prompt patches, guardrail specs, and
    # architectural changes targeted at each affected component. The v2
    # scaffold path returns an empty plan.

    extension_map = {
        "text": ".txt",
        "json": ".json",
        "markdown": ".md",
        "sarif": ".sarif",
    }

    if output is None:
        _print_findings(
            findings,
            effective_formats[0],
            meta,
            remediation_plan=remediation_plan,
            scenario_records=scenario_records,
            scan_outcome=scan_outcome,
            input_tokens_used=input_tokens_used,
            output_tokens_used=output_tokens_used,
            token_usage=token_usage,
            coverage_tracker=coverage_tracker,
        )
    else:
        for fmt in effective_formats:
            out_path = output_path_for_format(
                output,
                fmt=fmt,
                all_formats=effective_formats,
                extension_map=extension_map,
            )
            if fmt == "text":
                # Plain-text report — no ANSI escapes in a file. Identical
                # content to what ``nuguard redteam`` emits to stdout.
                out_path.write_text(
                    _render_findings_text(findings, meta, scan_outcome, colour=False),
                    encoding="utf-8",
                )
            elif fmt == "markdown":
                out_path.write_text(
                    _findings_to_markdown(
                        findings,
                        meta,
                        remediation_plan=remediation_plan,
                        scenario_records=scenario_records,
                        catalog_coverage=catalog_coverage,
                        coverage_tracker=coverage_tracker,
                    ),
                    encoding="utf-8",
                )
            elif fmt == "sarif":
                from nuguard.output.sarif_generator import generate_sarif  # noqa: PLC0415

                out_path.write_text(generate_sarif(findings, sbom_path=sbom_path), encoding="utf-8")
            else:
                from nuguard.redteam.report import to_json as _to_json_report  # noqa: PLC0415

                payload: dict = json.loads(
                    _to_json_report(
                        findings,
                        meta=meta,
                        remediation_plan=remediation_plan,
                        scan_outcome=scan_outcome,
                        token_usage=token_usage,
                        scenario_records=scenario_records,
                    )
                )
                if config_notes:
                    payload["config_notes"] = config_notes
                out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            typer.echo(f"Findings written to {out_path}")

        # Write machine-readable remediation plan alongside the main output
        if findings:
            from nuguard.output.json_generator import write_remediation_plan as _write_rp
            rp_path = output.parent / (output.stem + ".remediation-plan.json")
            try:
                _write_rp(
                    findings=findings,
                    output_path=rp_path,
                    target_url=target_url or "",
                )
                typer.echo(f"Remediation plan written to {rp_path}")
            except Exception as exc:
                _log.warning("Failed to write remediation plan: %s", exc)

    # Emit pytest regression test files if configured
    if cfg.emit_pytest and findings:
        from pathlib import Path as _Path  # noqa: PLC0415

        from nuguard.output.pytest_emitter import emit_regression_tests  # noqa: PLC0415
        try:
            written = emit_regression_tests(
                findings=findings,
                target_url=target_url or "",
                output_dir=_Path(cfg.emit_pytest_dir),
            )
            if written:
                typer.echo(
                    f"Regression tests written to {cfg.emit_pytest_dir}/ "
                    f"({len(written)} file(s))"
                )
        except Exception as exc:
            _log.warning("Failed to emit pytest regression tests: %s", exc)

    # Exit code
    _fail_on_severity(findings, effective_fail_on)


def _resolve_target_url(sbom_doc: object, launch: bool = False) -> str | None:
    """Pick the best target URL from the SBOM without launching anything.

    Priority: local_url (when --launch) → staging_urls → production_urls → deployment_urls.
    """
    try:
        from nuguard.redteam.launcher.app_launcher import pick_target_url

        prefer = "local" if launch else "staging"
        url = pick_target_url(sbom_doc, prefer=prefer)  # type: ignore[arg-type]
        if url:
            _log.info("SBOM-discovered target URL: %s", url)
            typer.echo(f"  Target URL (from SBOM): {url}")
        return url
    except Exception as exc:
        _log.debug("URL discovery from SBOM failed: %s", exc)
        return None


async def _run_redteam_v2(
    *,
    sbom_doc: object,
    sbom_path: Path | None,
    policy_path: Path | None,
    target_url: str | None,
    canary_path: Path | None,
    settings: "RedteamV2Settings",
    profile: str = "ci",
    chat_path: str = "",
    auth_config: "AuthConfig | None" = None,
    chat_payload_extras: "dict[str, Any] | None" = None,
    redteam_llm_model: str | None = None,
    redteam_llm_api_key: str | None = None,
    redteam_llm_api_base: str | None = None,
    eval_llm_model: str | None = None,
    eval_llm_api_key: str | None = None,
    eval_llm_api_base: str | None = None,
    verbose: bool = False,
) -> "tuple[list, list, str, list[str], Any, int, int, Any, Any, str, str, list]":
    """Run the v2 red-team engine and adapt its result to the CLI report tuple.

    Phase 0: the v2 orchestrator is a scaffold that returns no findings.  This
    wrapper keeps the same 13-tuple shape as :func:`_run_redteam` so the shared
    output/reporting code path is reused unchanged.
    """
    from nuguard.common.llm_client import LLMClient
    from nuguard.redteam.persona import EVAL_EXPERT_SYSTEM_PROMPT, REDTEAM_EXPERT_SYSTEM_PROMPT
    from nuguard.redteam.v2 import RedteamV2Orchestrator

    redteam_llm: LLMClient | None = None
    if redteam_llm_model:
        redteam_llm = LLMClient(
            model=redteam_llm_model,
            api_key=redteam_llm_api_key,
            api_base=redteam_llm_api_base,
            default_system_prompt=REDTEAM_EXPERT_SYSTEM_PROMPT,
        )
    eval_llm: LLMClient | None = None
    if eval_llm_model and eval_llm_api_key:
        eval_llm = LLMClient(
            model=eval_llm_model,
            api_key=eval_llm_api_key,
            api_base=eval_llm_api_base,
            default_system_prompt=EVAL_EXPERT_SYSTEM_PROMPT,
        )

    orchestrator = RedteamV2Orchestrator(
        sbom=sbom_doc,
        target_url=target_url or "",
        settings=settings,
        profile=profile,
        chat_path=chat_path,
        auth_config=auth_config,
        chat_payload_extras=chat_payload_extras,
        redteam_llm=redteam_llm,
        eval_llm=eval_llm,
        verbose=verbose,
    )
    try:
        result = await orchestrator.run()
    finally:
        try:
            from litellm.llms.custom_httpx.async_client_cleanup import (
                close_litellm_async_clients,  # noqa: PLC0415
            )
            await close_litellm_async_clients()
        except Exception:
            pass
    return (
        result.findings,
        result.scenario_records,
        result.scan_outcome,
        result.config_notes,
        None,
        result.token_usage.input_tokens,
        result.token_usage.output_tokens,
        None,
        result.token_usage,
        result.resolved_chat_path,
        result.resolved_chat_path_source,
        [],
    )


async def _run_redteam(
    sbom_doc: object,
    sbom_path: Path | None,
    policy_path: Path | None,
    target_url: str | None,
    canary_path: Path | None,
    profile: str,
    min_impact_score: float,
    scenario_filter: list[str] | None = None,
    auth_config: "AuthConfig | None" = None,
    headers_override: dict[str, str] | None = None,
    source_dir: Path | None = None,
    launch: bool = False,
    chat_path: str = "/chat",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
    chat_response_key: str | None = None,
    guided_conversations: bool = True,
    guided_max_turns: int = 12,
    guided_concurrency: int = 3,
    guided_mutation_mode: str = "hard",
    tree_breadth: int = 0,
    tree_max_depth: int = 0,
    strict_outcome: bool = False,
    redteam_llm_model: str | None = None,
    redteam_llm_api_key: str | None = None,
    redteam_llm_api_base: str | None = None,
    eval_llm_model: str | None = None,
    eval_llm_api_key: str | None = None,
    eval_llm_api_base: str | None = None,
    remediation_llm_client: "LLMClient | None" = None,
    prompt_cache_dir: str | None = None,
    finding_triggers: "RedteamFindingTriggers | None" = None,
    verbose: bool = False,
    credentials: dict[str, str] | None = None,
    scenario_timeout: float = 300.0,
    concurrency: int = 5,
    turn_delay_seconds: float = 0.0,
    scenario_delay_seconds: float = 0.0,
    similar_miss_threshold: int = 4,
    hard_refusal_abort_turns: int = 5,
    stall_abort_threshold: int = 8,
    skip_discovery: bool = False,
    discovery_max_turns: int = 3,
    chat_payload_extras: dict[str, Any] | None = None,
    catalog: "tuple | None" = None,
    pre_run_warmup: int = 0,
    verify_findings: bool = False,
    golden_data: "dict | None" = None,
    suppress_spa_html_auth_bypass: bool = True,
    codegen_escalation_enabled: bool = True,
) -> "tuple[list, list, str, list[str], Any, int, int, Any, Any, str, str, list]":
    from nuguard.models.policy import CognitivePolicy
    from nuguard.redteam.target.canary import CanaryConfig

    # Default behavior: auto-enrich low-confidence SBOMs before scenario generation.
    if hasattr(sbom_doc, "nodes") and hasattr(sbom_doc, "edges"):
        from nuguard.cli.common import enrich_sbom_for_run

        sbom_doc = await enrich_sbom_for_run(
            sbom=sbom_doc,  # type: ignore[arg-type]
            sbom_path=sbom_path,
            target_url=target_url,
            llm_enabled=bool(redteam_llm_model or eval_llm_model),
            llm_model=redteam_llm_model or eval_llm_model,
            llm_api_key=redteam_llm_api_key or eval_llm_api_key,
            llm_api_base=redteam_llm_api_base or eval_llm_api_base,
            probe_auth_header=auth_config.header if (auth_config and auth_config.type != "none") else None,
            log_prefix="redteam",
        )

    # Load policy + compiled controls
    cognitive_policy: CognitivePolicy | None = None
    policy_controls: list | None = None
    if policy_path and policy_path.exists():
        try:
            from nuguard.policy.loader import ensure_policy_controls

            cognitive_policy, policy_controls = await ensure_policy_controls(
                policy_path,
                use_llm=False,  # rule-based build on auto-creation; use 'nuguard policy compile --llm' for richer prompts
            )
        except NotImplementedError:
            _log.warning(
                "Policy parser not implemented; running without policy constraints"
            )
        except Exception as exc:
            _log.warning("Could not load policy %s: %s", policy_path, exc)

    # Load canary
    canary_config: CanaryConfig | None = None
    if canary_path and canary_path.exists():
        try:
            canary_config = CanaryConfig.load(canary_path)
            _log.info(
                "Loaded %d global canary watch values",
                len(canary_config.global_watch_values),
            )
        except Exception as exc:
            _log.warning("Could not load canary config: %s", exc)

    # Resolve auth and runtime headers with precedence:
    # 1) explicit full header map override
    # 2) structured/legacy auth config
    from nuguard.common.auth_runtime import resolve_auth_runtime

    auth_runtime = resolve_auth_runtime(
        auth_config=auth_config,
        headers_override=headers_override,
    )
    auth_config = auth_runtime.auth_config
    extra_headers: dict[str, str] = auth_runtime.initial_headers

    # Auto-launch the app if requested
    if launch:
        from nuguard.redteam.launcher.app_launcher import AppLauncher, AppLaunchError

        if source_dir is None:
            raise typer.BadParameter(
                "--source is required when --launch is set", param_hint="--source"
            )
        try:
            launcher = AppLauncher.from_sbom(sbom_doc, source_dir)  # type: ignore[arg-type]
            effective_url = target_url or launcher.url
        except AppLaunchError as exc:
            _log.error("Failed to prepare app launcher: %s", exc)
            raise typer.Exit(code=1) from exc

        typer.echo(f"  Launching app: {launcher._command}")
        typer.echo(f"  Local URL    : {launcher.url}")

        async with launcher:
            return await _run_orchestrator(
                sbom_doc=sbom_doc,
                target_url=effective_url,
                cognitive_policy=cognitive_policy,
                policy_controls=policy_controls,
                canary_config=canary_config,
                profile=profile,
                min_impact_score=min_impact_score,
                scenario_filter=scenario_filter,
                chat_path=chat_path,
                chat_payload_key=chat_payload_key,
                chat_payload_list=chat_payload_list,
                chat_response_key=chat_response_key,
                chat_payload_extras=chat_payload_extras,
                guided_conversations=guided_conversations,
                guided_max_turns=guided_max_turns,
                guided_concurrency=guided_concurrency,
                guided_mutation_mode=guided_mutation_mode,
                auth_config=auth_config,
                tree_breadth=tree_breadth,
                tree_max_depth=tree_max_depth,
                extra_headers=extra_headers or None,
                strict_outcome=strict_outcome,
                redteam_llm_model=redteam_llm_model,
                redteam_llm_api_key=redteam_llm_api_key,
                redteam_llm_api_base=redteam_llm_api_base,
                eval_llm_model=eval_llm_model,
                eval_llm_api_key=eval_llm_api_key,
                eval_llm_api_base=eval_llm_api_base,
                remediation_llm_client=remediation_llm_client,
                prompt_cache_dir=prompt_cache_dir,
                finding_triggers=finding_triggers,
                verbose=verbose,
                credentials=credentials,
                scenario_timeout=scenario_timeout,
                concurrency=concurrency,
                turn_delay_seconds=turn_delay_seconds,
                scenario_delay_seconds=scenario_delay_seconds,
                similar_miss_threshold=similar_miss_threshold,
                hard_refusal_abort_turns=hard_refusal_abort_turns,
                stall_abort_threshold=stall_abort_threshold,
                skip_discovery=skip_discovery,
                discovery_max_turns=discovery_max_turns,
                catalog=catalog,
                pre_run_warmup=pre_run_warmup,
                verify_findings=verify_findings,
                golden_data=golden_data,
                suppress_spa_html_auth_bypass=suppress_spa_html_auth_bypass,
                codegen_escalation_enabled=codegen_escalation_enabled,
            )

    # App already running — just scan
    assert target_url is not None, "target_url must be set when launch=False"
    return await _run_orchestrator(
        sbom_doc=sbom_doc,
        target_url=target_url,
        cognitive_policy=cognitive_policy,
        policy_controls=policy_controls,
        canary_config=canary_config,
        profile=profile,
        min_impact_score=min_impact_score,
        scenario_filter=scenario_filter,
        chat_path=chat_path,
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
        chat_response_key=chat_response_key,
        chat_payload_extras=chat_payload_extras,
        guided_conversations=guided_conversations,
        guided_max_turns=guided_max_turns,
        guided_concurrency=guided_concurrency,
        guided_mutation_mode=guided_mutation_mode,
        tree_breadth=tree_breadth,
        tree_max_depth=tree_max_depth,
        auth_config=auth_config,
        extra_headers=extra_headers or None,
        strict_outcome=strict_outcome,
        redteam_llm_model=redteam_llm_model,
        redteam_llm_api_key=redteam_llm_api_key,
        redteam_llm_api_base=redteam_llm_api_base,
        eval_llm_model=eval_llm_model,
        eval_llm_api_key=eval_llm_api_key,
        eval_llm_api_base=eval_llm_api_base,
        remediation_llm_client=remediation_llm_client,
        prompt_cache_dir=prompt_cache_dir,
        finding_triggers=finding_triggers,
        verbose=verbose,
        credentials=credentials,
        scenario_timeout=scenario_timeout,
        concurrency=concurrency,
        turn_delay_seconds=turn_delay_seconds,
        scenario_delay_seconds=scenario_delay_seconds,
        similar_miss_threshold=similar_miss_threshold,
        hard_refusal_abort_turns=hard_refusal_abort_turns,
        stall_abort_threshold=stall_abort_threshold,
        skip_discovery=skip_discovery,
        discovery_max_turns=discovery_max_turns,
        catalog=catalog,
        pre_run_warmup=pre_run_warmup,
        verify_findings=verify_findings,
        golden_data=golden_data,
        suppress_spa_html_auth_bypass=suppress_spa_html_auth_bypass,
        codegen_escalation_enabled=codegen_escalation_enabled,
    )


async def _run_orchestrator(  # noqa: C901
    sbom_doc: object,
    target_url: str,
    cognitive_policy: object,
    canary_config: object,
    profile: str,
    min_impact_score: float,
    scenario_filter: list[str] | None,
    policy_controls: list | None = None,
    chat_path: str = "/chat",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
    chat_response_key: str | None = None,
    chat_payload_extras: dict[str, Any] | None = None,
    guided_conversations: bool = True,
    guided_max_turns: int = 12,
    guided_concurrency: int = 3,
    guided_mutation_mode: str = "hard",
    tree_breadth: int = 0,
    tree_max_depth: int = 0,
    extra_headers: dict[str, str] | None = None,
    auth_config: "AuthConfig | None" = None,
    strict_outcome: bool = False,
    redteam_llm_model: str | None = None,
    redteam_llm_api_key: str | None = None,
    redteam_llm_api_base: str | None = None,
    eval_llm_model: str | None = None,
    eval_llm_api_key: str | None = None,
    eval_llm_api_base: str | None = None,
    remediation_llm_client: "LLMClient | None" = None,
    prompt_cache_dir: str | None = None,
    finding_triggers: "RedteamFindingTriggers | None" = None,
    verbose: bool = False,
    credentials: dict[str, str] | None = None,
    scenario_timeout: float = 300.0,
    concurrency: int = 5,
    turn_delay_seconds: float = 0.0,
    scenario_delay_seconds: float = 0.0,
    similar_miss_threshold: int = 4,
    hard_refusal_abort_turns: int = 5,
    stall_abort_threshold: int = 8,
    skip_discovery: bool = False,
    discovery_max_turns: int = 3,
    catalog: "tuple | None" = None,
    pre_run_warmup: int = 0,
    verify_findings: bool = False,
    golden_data: "dict | None" = None,
    suppress_spa_html_auth_bypass: bool = True,
    codegen_escalation_enabled: bool = True,
) -> "tuple[list, list, str, list[str], Any, int, int, Any, Any, str, str, list]":
    from nuguard.common.llm_client import LLMClient
    from nuguard.redteam.persona import EVAL_EXPERT_SYSTEM_PROMPT, REDTEAM_EXPERT_SYSTEM_PROMPT
    from nuguard.redteam.public_api import RedteamRunRequest, run_redteam

    redteam_llm: LLMClient | None = None
    if redteam_llm_model:
        redteam_llm = LLMClient(
            model=redteam_llm_model,
            api_key=redteam_llm_api_key,
            api_base=redteam_llm_api_base,
            default_system_prompt=REDTEAM_EXPERT_SYSTEM_PROMPT,
        )
    eval_llm: LLMClient | None = None
    if eval_llm_model and eval_llm_api_key:
        eval_llm = LLMClient(
            model=eval_llm_model,
            api_key=eval_llm_api_key,
            api_base=eval_llm_api_base,
            default_system_prompt=EVAL_EXPERT_SYSTEM_PROMPT,
        )

    request = RedteamRunRequest(
        target_url=target_url,
        profile=profile,
        min_impact_score=min_impact_score,
        chat_path=chat_path,
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
        chat_response_key=chat_response_key,
        concurrency=concurrency,
        guided_conversations=guided_conversations,
        guided_max_turns=guided_max_turns,
        guided_concurrency=guided_concurrency,
        guided_mutation_mode=guided_mutation_mode,
        tree_breadth=tree_breadth,
        tree_max_depth=tree_max_depth,
        extra_headers=extra_headers,
        strict_outcome=strict_outcome,
        scenario_filter=scenario_filter,
        canary_config=canary_config,  # type: ignore[arg-type]
        auth_config=auth_config,
        finding_triggers=finding_triggers,
        verbose=verbose,
        credentials=credentials,
        scenario_timeout=scenario_timeout,
        turn_delay_seconds=turn_delay_seconds,
        scenario_delay_seconds=scenario_delay_seconds,
        similar_miss_threshold=similar_miss_threshold,
        hard_refusal_abort_turns=hard_refusal_abort_turns,
        stall_abort_threshold=stall_abort_threshold,
        skip_discovery=skip_discovery,
        discovery_max_turns=discovery_max_turns,
        chat_payload_extras=chat_payload_extras or None,
        pre_run_warmup=pre_run_warmup,
        verify_findings=verify_findings,
        golden_data=golden_data,
        suppress_spa_html_auth_bypass=suppress_spa_html_auth_bypass,
        codegen_escalation_enabled=codegen_escalation_enabled,
    )

    result = await run_redteam(
        request,
        sbom=sbom_doc,  # type: ignore[arg-type]
        policy=cognitive_policy,  # type: ignore[arg-type]
        policy_controls=policy_controls,
        redteam_llm=redteam_llm,
        eval_llm=eval_llm,
        remediation_llm_client=remediation_llm_client,
        catalog=catalog,
        prompt_cache_dir=Path(prompt_cache_dir) if prompt_cache_dir else None,
    )

    for note in result.config_notes:
        typer.echo(f"\n⚠ {note}", err=True)

    return (
        result.findings,
        result.scenario_records,
        result.scan_outcome,
        result.config_notes,
        result.catalog_coverage,
        result.input_tokens_used,
        result.output_tokens_used,
        result.coverage_tracker,
        result.token_usage,
        result.resolved_chat_path,
        result.resolved_chat_path_source,
        result.remediation_plan,
    )


_SEV_COLOUR: dict[str, str] = {
    "critical": "red",
    "high": "red",
    "medium": "yellow",
    "low": "blue",
    "info": "white",
}


def _render_findings_text(
    findings: list,
    meta: "ReportMeta",
    scan_outcome: str = "no_findings",
    *,
    colour: bool = False,
) -> str:
    """Render findings as a plain-text report.

    Returns a multi-line string suitable for writing to a file or echoing to a
    terminal. When ``colour`` is ``True``, the severity label is wrapped in
    ANSI escapes via :func:`typer.style` (used for stdout). When ``False``
    (default), the output is plain text with no escape sequences — suitable
    for on-disk reports that downstream tools may parse.

    Args:
        findings: List of :class:`~nuguard.models.finding.Finding` objects.
        meta: Report metadata header.
        scan_outcome: One-line scan outcome string ("no_findings", etc.).
        colour: If ``True``, wrap severity labels with ANSI colour codes.

    Returns:
        Plain-text report as a single string.
    """
    from nuguard.models.finding import Severity as _Severity

    if not findings:
        lines = [
            meta.to_text_line(),
            f"Outcome: {scan_outcome}",
            "No findings — scan complete",
        ]
        return "\n".join(lines) + "\n"

    _ORDER = [
        _Severity.CRITICAL,
        _Severity.HIGH,
        _Severity.MEDIUM,
        _Severity.LOW,
        _Severity.INFO,
    ]

    out: list[str] = []
    out.append("")
    out.append("─" * 60)
    out.append(f"  NuGuard Red-Team — {len(findings)} finding(s)")
    out.append(f"  {meta.to_text_line()}")
    out.append(f"  Outcome: {scan_outcome}")
    out.append("─" * 60)
    for f in sorted(findings, key=lambda x: _ORDER.index(x.severity)):
        sev_key = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
        sev_text = sev_key.upper()
        if colour:
            colour_name = _SEV_COLOUR.get(sev_key, "white")
            sev_label = typer.style(sev_text, fg=colour_name, bold=True)
        else:
            sev_label = sev_text
        out.append("")
        out.append(f"[{sev_label}] {f.title}")
        out.append(f"  {f.description[:200]}")
        if f.remediation:
            out.append(f"  Fix: {f.remediation[:150]}")
        if f.owasp_asi_ref:
            out.append(f"  Ref: {f.owasp_asi_ref}")
    out.append("")
    out.append("─" * 60)
    out.append("")
    return "\n".join(out)


def _print_findings(
    findings: list,
    format: str,
    meta: ReportMeta | None = None,
    remediation_plan: list | None = None,
    scenario_records: list | None = None,
    scan_outcome: str = "no_findings",
    input_tokens_used: int = 0,
    output_tokens_used: int = 0,
    token_usage: "TokenUsage | None" = None,
    coverage_tracker: object | None = None,
) -> None:
    """Print findings to stdout in the requested format."""
    if meta is None:
        meta = ReportMeta()

    if format == "json":
        from nuguard.redteam.report import to_json as _to_json_report

        typer.echo(
            _to_json_report(
                findings,
                meta=meta,
                remediation_plan=remediation_plan,
                scan_outcome=scan_outcome,
                input_tokens_used=input_tokens_used,
                output_tokens_used=output_tokens_used,
                token_usage=token_usage,
                scenario_records=scenario_records,
            )
        )
        return

    if format == "sarif":
        from nuguard.output.sarif_generator import generate_sarif

        typer.echo(generate_sarif(findings))
        return

    if format == "markdown":
        typer.echo(
            _findings_to_markdown(findings, meta, remediation_plan=remediation_plan,
                                  scenario_records=scenario_records,
                                  coverage_tracker=coverage_tracker)
        )
        return

    # Default and explicit "text" path — emit the plain-text report (with
    # ANSI colour escapes when stdout is a TTY).
    typer.echo(_render_findings_text(findings, meta, scan_outcome, colour=True))


def _scenario_coverage_table(scenario_records: list) -> list[str]:
    """Delegate to :func:`nuguard.redteam.report._scenario_coverage_table`."""
    from nuguard.redteam.report import _scenario_coverage_table as _impl
    return _impl(scenario_records)


def _findings_to_markdown(
    findings: list,
    meta: ReportMeta | None = None,
    remediation_plan: list | None = None,
    scenario_records: list | None = None,
    catalog_coverage: object | None = None,
    coverage_tracker: object | None = None,
) -> str:
    """Delegate to :func:`nuguard.redteam.report.to_markdown`."""
    from nuguard.redteam.report import to_markdown
    return to_markdown(
        findings,
        meta=meta,
        remediation_plan=remediation_plan,
        scenario_records=scenario_records,
        catalog_coverage=catalog_coverage,
        coverage_tracker=coverage_tracker,
    )


def _append_remediation_plan(lines: list[str], remediation_plan: list) -> None:
    """Delegate to :func:`nuguard.output.report_shared.render_remediation_plan_section`."""
    from nuguard.output.report_shared import render_remediation_plan_section
    render_remediation_plan_section(lines, remediation_plan)


def _fail_on_severity(findings: list, fail_on: str) -> None:
    """Exit with code 2 if any finding meets or exceeds the threshold severity."""
    from nuguard.models.finding import Severity

    _ORDER = [
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.LOW,
        Severity.INFO,
    ]
    try:
        threshold = Severity(fail_on.lower())
    except ValueError:
        threshold = Severity.HIGH
    threshold_idx = _ORDER.index(threshold)

    for f in findings:
        try:
            if _ORDER.index(f.severity) <= threshold_idx:
                raise typer.Exit(code=2)
        except ValueError:
            pass


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

    The exported file can be edited and passed back via --catalog to replace
    the built-in catalog with a custom set of scenarios.

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
