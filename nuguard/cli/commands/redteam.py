"""nuguard redteam — dynamic adversarial testing against a live AI application."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig
    from nuguard.config import RedteamFindingTriggers

import typer

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
    profile: str = typer.Option(
        "ci", "--profile", help="Scan profile: ci (fast, safe) or full."
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
    format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text | json | markdown | sarif."
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
    sbom_path = sbom or (Path(cfg.sbom_path) if cfg.sbom_path else None)
    policy_path = policy or (Path(cfg.policy_path) if cfg.policy_path else None)
    target_url = target or cfg.target_url
    canary_path = canary or (Path(cfg.canary_path) if cfg.canary_path else None)
    _source_path_val = getattr(cfg, "source_path", None)
    source_dir = source or (Path(str(_source_path_val)) if _source_path_val else None)
    # CLI flag takes precedence; fall back to config default
    effective_profile = profile if profile != "ci" else cfg.redteam_profile
    effective_min_impact = (
        min_impact_score if min_impact_score != 0.0 else cfg.min_impact_score
    )
    effective_format = format if format != "text" else cfg.output_format
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

    try:
        findings, llm_remediations, scenario_records = asyncio.run(
            _run_redteam(
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
                # ^ eval_llm falls back to top-level llm.model/api_key when redteam.eval_llm is not set
                finding_triggers=finding_triggers,
                verbose=effective_verbose,
            )
        )
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
        finding_triggers=finding_triggers.model_dump(),
    )
    # Synthesize per-SBOM-node remediation artefacts — best-effort, never
    # blocks the report if it fails.  The synthesizer consults the SBOM to
    # produce concrete system-prompt patches, guardrail specs, and
    # architectural changes targeted at each affected component.
    remediation_plan = _build_redteam_remediation_plan(
        findings, sbom_doc, cognitive_policy=None
    )

    _print_findings(findings, effective_format, meta, remediation_plan=remediation_plan,
                    scenario_records=scenario_records)
    if output:
        if effective_format == "markdown":
            output.write_text(
                _findings_to_markdown(findings, meta, remediation_plan=remediation_plan,
                                      scenario_records=scenario_records),
                encoding="utf-8",
            )
        else:
            payload = {
                "_meta": meta.to_dict(),
                "findings": [f.model_dump() for f in findings],
                "remediation_plan": [a.model_dump() for a in remediation_plan],
            }
            output.write_text(json.dumps(payload, indent=2, default=str))
        typer.echo(f"Findings written to {output}")

        # Write machine-readable remediation plan alongside the main output
        if llm_remediations and findings:
            from nuguard.output.json_generator import write_remediation_plan as _write_rp
            rp_path = output.parent / (output.stem + ".remediation-plan.json")
            try:
                _write_rp(
                    findings=findings,
                    remediations=llm_remediations,
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
    finding_triggers: "RedteamFindingTriggers | None" = None,
    verbose: bool = False,
    credentials: dict[str, str] | None = None,
) -> tuple[list, dict[str, str], list]:
    """Async inner function: optionally launch the app, then run the orchestrator."""
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
            from nuguard.policy.loader import compiled_path_for, load_controls
            from nuguard.policy.parser import parse_policy

            cognitive_policy = parse_policy(policy_path.read_text())

            compiled = compiled_path_for(policy_path)
            if compiled.exists():
                _log.info("Loading compiled policy controls from %s", compiled)
                policy_controls = load_controls(compiled)
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
                finding_triggers=finding_triggers,
                verbose=verbose,
                credentials=credentials,
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
        finding_triggers=finding_triggers,
        verbose=verbose,
        credentials=credentials,
    )


async def _run_orchestrator(
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
    finding_triggers: "RedteamFindingTriggers | None" = None,
    verbose: bool = False,
    credentials: dict[str, str] | None = None,
) -> tuple[list, dict[str, str], list]:
    from nuguard.common.llm_client import LLMClient
    from nuguard.redteam.executor.orchestrator import RedteamOrchestrator

    redteam_llm: LLMClient | None = None
    if redteam_llm_model:
        redteam_llm = LLMClient(model=redteam_llm_model, api_key=redteam_llm_api_key, api_base=redteam_llm_api_base)
    eval_llm: LLMClient | None = None
    if eval_llm_model and eval_llm_api_key:
        eval_llm = LLMClient(model=eval_llm_model, api_key=eval_llm_api_key, api_base=eval_llm_api_base)

    orchestrator = RedteamOrchestrator(
        sbom=sbom_doc,  # type: ignore[arg-type]
        target_url=target_url,
        policy=cognitive_policy,  # type: ignore[arg-type]
        policy_controls=policy_controls,
        canary_config=canary_config,  # type: ignore[arg-type]
        profile=profile,
        min_impact_score=min_impact_score,
        chat_path=chat_path,
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
        chat_response_key=chat_response_key,
        guided_conversations=guided_conversations,
        guided_max_turns=guided_max_turns,
        guided_concurrency=guided_concurrency,
        guided_mutation_mode=guided_mutation_mode,
        tree_breadth=tree_breadth,
        tree_max_depth=tree_max_depth,
        extra_headers=extra_headers,
        auth_config=auth_config,
        strict_outcome=strict_outcome,
        scenario_filter=scenario_filter,
        redteam_llm=redteam_llm,
        eval_llm=eval_llm,
        finding_triggers=finding_triggers,
        verbose=verbose,
        credentials=credentials,
    )
    findings = await orchestrator.run()
    llm_remediations: dict[str, str] = orchestrator.llm_remediations
    scenario_records = orchestrator.scenario_records

    # Apply scenario filter if provided
    if scenario_filter:
        findings = [
            f for f in findings
            if not f.goal_type
            or any(
                s.lower().replace("-", "_") in (f.goal_type or "").lower()
                for s in scenario_filter
            )
        ]

    return findings, llm_remediations, scenario_records


def _print_findings(
    findings: list,
    format: str,
    meta: ReportMeta | None = None,
    remediation_plan: list | None = None,
    scenario_records: list | None = None,
) -> None:
    """Print findings to stdout in the requested format."""
    from nuguard.models.finding import Severity

    if meta is None:
        meta = ReportMeta()

    if format == "json":
        payload = {
            "_meta": meta.to_dict(),
            "findings": [f.model_dump() for f in findings],
            "remediation_plan": [a.model_dump() for a in (remediation_plan or [])],
        }
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    if format == "markdown":
        typer.echo(
            _findings_to_markdown(findings, meta, remediation_plan=remediation_plan,
                                  scenario_records=scenario_records)
        )
        return

    if not findings:
        typer.echo(meta.to_text_line())
        typer.echo("No findings — scan complete")
        return

    _SEV_COLOUR = {
        Severity.CRITICAL: "red",
        Severity.HIGH: "red",
        Severity.MEDIUM: "yellow",
        Severity.LOW: "blue",
        Severity.INFO: "white",
    }

    typer.echo(f"\n{'─' * 60}")
    typer.echo(f"  NuGuard Red-Team — {len(findings)} finding(s)")
    typer.echo(f"  {meta.to_text_line()}")
    typer.echo(f"{'─' * 60}")
    for f in sorted(findings, key=lambda x: list(Severity).index(x.severity)):
        colour = _SEV_COLOUR.get(f.severity, "white")
        sev_label = typer.style(f.severity.upper(), fg=colour, bold=True)
        typer.echo(f"\n[{sev_label}] {f.title}")
        typer.echo(f"  {f.description[:200]}")
        if f.remediation:
            typer.echo(f"  Fix: {f.remediation[:150]}")
        if f.owasp_asi_ref:
            typer.echo(f"  Ref: {f.owasp_asi_ref}")
    typer.echo(f"\n{'─' * 60}\n")


def _scenario_coverage_table(scenario_records: list) -> list[str]:
    """Return Markdown lines for the Scenario Coverage summary table.

    One row per executed scenario, sorted by impact score descending.
    Columns: rank, title, goal, finding (YES/no), turns used / budget,
    duration, avg time per turn.

    A summary line below the table shows aggregate stats.
    """
    if not scenario_records:
        return []

    # Sort by impact_score desc; within same score, findings first
    records = sorted(
        scenario_records,
        key=lambda r: (-getattr(r, "impact_score", 0.0), 0 if r.had_finding else 1),
    )

    # Abbreviate goal_type labels so they fit in the table
    _GOAL_ABBREV = {
        "DATA_EXFILTRATION": "Data Exfil",
        "PRIVILEGE_ESCALATION": "Priv Esc",
        "PROMPT_DRIVEN_THREAT": "Prompt Threat",
        "POLICY_VIOLATION": "Policy Viol",
        "TOOL_ABUSE": "Tool Abuse",
        "API_ATTACK": "API Attack",
        "MCP_TOXIC_FLOW": "MCP Toxic",
    }

    def _fmt_duration(s: float) -> str:
        if s <= 0:
            return "—"
        return f"{s:.1f}s"

    def _fmt_avg(s: float, turns: int) -> str:
        if s <= 0 or turns <= 0:
            return "—"
        return f"{s / turns:.1f}s"

    lines: list[str] = ["## Scenario Coverage", ""]
    lines.append(
        "| # | Scenario | Goal | Finding | Turns | Duration | Avg/Turn |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|"
    )

    total_duration = 0.0
    total_turns = 0
    findings_count = 0

    for idx, r in enumerate(records, start=1):
        title = r.title[:60] + ("…" if len(r.title) > 60 else "")
        goal = _GOAL_ABBREV.get(r.goal_type, r.goal_type.replace("_", " ").title())
        finding_cell = "**YES**" if r.had_finding else "no"
        turns_used = getattr(r, "turns_used", len(r.steps))
        turns_budget = getattr(r, "turns_budget", 0) or turns_used
        turns_cell = f"{turns_used}/{turns_budget}"
        duration = getattr(r, "duration_s", 0.0)
        dur_cell = _fmt_duration(duration)
        avg_cell = _fmt_avg(duration, turns_used)

        total_duration += duration
        total_turns += turns_used
        if r.had_finding:
            findings_count += 1

        lines.append(
            f"| {idx} | {title} | {goal} | {finding_cell} "
            f"| {turns_cell} | {dur_cell} | {avg_cell} |"
        )

    n = len(records)
    avg_scenario = _fmt_duration(total_duration / n if n else 0)
    avg_turn = _fmt_avg(total_duration, total_turns)
    lines.append("")
    lines.append(
        f"_{n} scenario(s) executed — {findings_count} finding(s). "
        f"Total: {_fmt_duration(total_duration)} | "
        f"Avg per scenario: {avg_scenario} | Avg per turn: {avg_turn}_"
    )
    lines.append("")
    return lines


def _findings_to_markdown(
    findings: list,
    meta: ReportMeta | None = None,
    remediation_plan: list | None = None,
    scenario_records: list | None = None,
) -> str:
    """Render redteam findings as a Markdown report string.

    When *remediation_plan* is supplied (a list of ``RemediationArtefact``
    objects produced by :class:`RemediationSynthesizer`), a
    ``## Remediation Plan`` section is appended with concrete, SBOM-node
    specific patches, guardrails and architectural changes grouped by
    component — matching the behavior report's layout.

    When *scenario_records* is supplied (a list of ``ScenarioRecord`` objects
    from the orchestrator), a ``## Scenario Coverage`` table is inserted
    immediately after the report header, before the per-finding detail.
    """
    if meta is None:
        meta = ReportMeta()
    lines: list[str] = ["# NuGuard Red-Team Report", ""]
    lines += meta.to_markdown_lines()

    if scenario_records:
        lines += _scenario_coverage_table(scenario_records)

    if not findings:
        lines += ["_No findings — scan complete._", ""]
        return "\n".join(lines)

    from nuguard.models.finding import Severity

    lines += [f"**{len(findings)} finding(s)**", ""]
    for f in sorted(findings, key=lambda x: list(Severity).index(x.severity)):
        sev = f.severity.value.upper() if hasattr(f.severity, "value") else str(f.severity).upper()
        lines += [f"## [{sev}] {f.title}", ""]
        lines += [f.description, ""]
        if f.affected_component:
            lines += [f"**Component:** {f.affected_component}", ""]
        if f.goal_type:
            lines += [f"**Type:** {f.goal_type}", ""]
        # Progress score (1–5 rubric)
        scores = getattr(f, "scores", None) or {}
        progress = scores.get("goal_progress")
        if progress is not None:
            lines += [f"**Progress score:** {progress}/5", ""]
        # One-sentence reasoning from the judge
        reasoning = getattr(f, "reasoning", "") or ""
        if reasoning:
            lines += [f"**Finding reasoning:** {reasoning}", ""]
        # Named success indicator
        success_indicator = getattr(f, "success_indicator", None)
        if success_indicator:
            lines += [f"**Success indicator:** `{success_indicator}`", ""]
        # Evidence quote — exact substring proving the breach
        evidence_quote = getattr(f, "evidence_quote", "") or ""
        if evidence_quote:
            lines += [
                "**Evidence quote:**",
                "> " + evidence_quote.replace("\n", "\n> "),
                "",
            ]
        if f.remediation:
            lines += [f"**Remediation:** {f.remediation}", ""]
        if f.owasp_asi_ref:
            lines += [f"**OWASP ASI:** {f.owasp_asi_ref}", ""]
        if f.owasp_llm_ref:
            lines += [f"**OWASP LLM:** {f.owasp_llm_ref}", ""]
        if f.evidence:
            lines += [
                "**Evidence:**",
                "```",
                _truncate_evidence(f.evidence, limit=2500),
                "```",
                "",
            ]

    if remediation_plan:
        _append_remediation_plan(lines, remediation_plan)
    return "\n".join(lines)


def _append_remediation_plan(lines: list[str], remediation_plan: list) -> None:
    """Append a Remediation Plan section to *lines*, grouped by SBOM node.

    Mirrors the behavior report's remediation layout so the developer sees
    the same actionable, per-node patch / guardrail / architectural-change
    artefacts regardless of which tool produced the finding.
    """
    # Reuse the behavior report's renderer so the format stays identical.
    from nuguard.behavior.report import _render_artefact

    lines.append("## Remediation Plan")
    lines.append("")
    lines.append(
        "Concrete, SBOM-node-specific remediations generated from the findings "
        "above. Apply in priority order."
    )
    lines.append("")

    # Group by affected component so developers see every change they need
    # to make to a single agent/tool in one place.
    by_component: dict[str, list] = {}
    for art in remediation_plan:
        by_component.setdefault(art.component, []).append(art)

    for comp, arts in by_component.items():
        lines.append(f"### {comp}")
        lines.append("")
        for art in arts:
            _render_artefact(lines, art)


def _build_redteam_remediation_plan(
    findings: list,
    sbom_doc: object,
    cognitive_policy: object | None,
    llm_client: object | None = None,
) -> list:
    """Synthesize per-SBOM-node remediation artefacts from redteam findings.

    Returns ``[]`` if no SBOM is available or synthesis fails — the caller
    should treat this as a best-effort enrichment, not a hard dependency.
    """
    if sbom_doc is None or not findings:
        return []
    try:
        from nuguard.behavior.remediation import RemediationSynthesizer

        synthesizer = RemediationSynthesizer(
            sbom=sbom_doc,  # type: ignore[arg-type]
            policy=cognitive_policy,  # type: ignore[arg-type]
            llm_client=llm_client,  # type: ignore[arg-type]
        )
        # Convert Finding objects to dicts the synthesizer's classifier
        # understands.  ``goal_type`` is key — it lets the classifier route
        # directly to the correct remediation handler without heuristics.
        finding_dicts = [
            {
                "finding_id": f.finding_id,
                "title": f.title,
                "description": f.description or "",
                "affected_component": f.affected_component or "unknown",
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                "goal_type": f.goal_type or "",
            }
            for f in findings
        ]
        return synthesizer.synthesize_findings(finding_dicts)
    except Exception as exc:
        _log.warning("remediation synthesis failed — skipping plan: %s", exc)
        return []


def _truncate_evidence(text: str, *, limit: int = 2500) -> str:
    """Trim *text* to ``limit`` chars at a newline/word boundary.

    The previous implementation did ``text[:500]`` which cut mid-word and
    — worse — kept only the opening warmup turn of multi-turn evidence.
    This variant prefers breaking on a newline, falling back to a word
    boundary, and only hard-cuts as a last resort.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer a newline break within the last ~20% of the window — keeps
    # turn boundaries intact when the evidence is a transcript.
    window = max(200, limit // 5)
    last_nl = cut.rfind("\n", limit - window)
    if last_nl != -1:
        return cut[:last_nl] + "\n… (truncated)"
    last_sp = cut.rfind(" ", limit - 80)
    if last_sp != -1:
        return cut[:last_sp] + " … (truncated)"
    return cut + "… (truncated)"


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
