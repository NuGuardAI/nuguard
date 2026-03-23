"""nuguard redteam — dynamic adversarial testing against a live AI application."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

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
        None, "--config", help="Path to nuguard.yaml config file."
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
        None, "--output", help="Write findings JSON to this path."
    ),
    format: str = typer.Option(
        "text", "--format", help="Output format: text | json | sarif."
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
    source_dir = source or (Path(cfg.source_path) if getattr(cfg, "source_path", None) else None)
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

    findings = asyncio.run(
        _run_redteam(
            sbom_doc=sbom_doc,
            policy_path=policy_path,
            target_url=target_url,
            canary_path=canary_path,
            profile=effective_profile,
            min_impact_score=effective_min_impact,
            scenario_filter=effective_scenarios,
            auth_header=cfg.redteam_auth_header,
            source_dir=source_dir,
            launch=launch,
            chat_path=cfg.target_endpoint,
            chat_payload_key=cfg.redteam_chat_payload_key,
            chat_payload_list=cfg.redteam_chat_payload_list,
            guided_conversations=effective_guided,
            guided_max_turns=effective_guided_max_turns,
            guided_concurrency=effective_guided_concurrency,
            strict_outcome=cfg.redteam_strict_outcome,
            redteam_llm_model=cfg.redteam_llm_model,
            redteam_llm_api_key=cfg.redteam_llm_api_key,
            eval_llm_model=cfg.redteam_eval_llm_model,
            eval_llm_api_key=cfg.redteam_eval_llm_api_key,
        )
    )

    # Output
    _print_findings(findings, effective_format)
    if output:
        output.write_text(
            json.dumps([f.model_dump() for f in findings], indent=2, default=str)
        )
        typer.echo(f"Findings written to {output}")

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
    policy_path: Path | None,
    target_url: str | None,
    canary_path: Path | None,
    profile: str,
    min_impact_score: float,
    scenario_filter: list[str] | None = None,
    auth_header: str | None = None,
    source_dir: Path | None = None,
    launch: bool = False,
    chat_path: str = "/chat",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
    guided_conversations: bool = True,
    guided_max_turns: int = 12,
    guided_concurrency: int = 3,
    strict_outcome: bool = False,
    redteam_llm_model: str | None = None,
    redteam_llm_api_key: str | None = None,
    eval_llm_model: str | None = None,
    eval_llm_api_key: str | None = None,
) -> list:
    """Async inner function: optionally launch the app, then run the orchestrator."""
    from nuguard.models.policy import CognitivePolicy
    from nuguard.redteam.target.canary import CanaryConfig

    # Load policy
    cognitive_policy: CognitivePolicy | None = None
    if policy_path and policy_path.exists():
        try:
            from nuguard.policy.parser import parse_policy

            cognitive_policy = parse_policy(policy_path.read_text())
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

    # Build extra headers from auth_header config (format: "Header-Name: value")
    extra_headers: dict[str, str] = {}
    if auth_header:
        if ":" in auth_header:
            hname, _, hval = auth_header.partition(":")
            extra_headers[hname.strip()] = hval.strip()

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
                canary_config=canary_config,
                profile=profile,
                min_impact_score=min_impact_score,
                scenario_filter=scenario_filter,
                chat_path=chat_path,
                chat_payload_key=chat_payload_key,
                chat_payload_list=chat_payload_list,
                guided_conversations=guided_conversations,
                guided_max_turns=guided_max_turns,
                guided_concurrency=guided_concurrency,
                extra_headers=extra_headers or None,
                strict_outcome=strict_outcome,
                redteam_llm_model=redteam_llm_model,
                redteam_llm_api_key=redteam_llm_api_key,
                eval_llm_model=eval_llm_model,
                eval_llm_api_key=eval_llm_api_key,
            )

    # App already running — just scan
    assert target_url is not None, "target_url must be set when launch=False"
    return await _run_orchestrator(
        sbom_doc=sbom_doc,
        target_url=target_url,
        cognitive_policy=cognitive_policy,
        canary_config=canary_config,
        profile=profile,
        min_impact_score=min_impact_score,
        scenario_filter=scenario_filter,
        chat_path=chat_path,
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
        guided_conversations=guided_conversations,
        guided_max_turns=guided_max_turns,
        guided_concurrency=guided_concurrency,
        extra_headers=extra_headers or None,
        strict_outcome=strict_outcome,
        redteam_llm_model=redteam_llm_model,
        redteam_llm_api_key=redteam_llm_api_key,
        eval_llm_model=eval_llm_model,
        eval_llm_api_key=eval_llm_api_key,
    )


async def _run_orchestrator(
    sbom_doc: object,
    target_url: str,
    cognitive_policy: object,
    canary_config: object,
    profile: str,
    min_impact_score: float,
    scenario_filter: list[str] | None,
    chat_path: str = "/chat",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
    guided_conversations: bool = True,
    guided_max_turns: int = 12,
    guided_concurrency: int = 3,
    extra_headers: dict[str, str] | None = None,
    strict_outcome: bool = False,
    redteam_llm_model: str | None = None,
    redteam_llm_api_key: str | None = None,
    eval_llm_model: str | None = None,
    eval_llm_api_key: str | None = None,
) -> list:
    from nuguard.common.llm_client import LLMClient
    from nuguard.redteam.executor.orchestrator import RedteamOrchestrator

    redteam_llm: LLMClient | None = None
    if redteam_llm_model:
        redteam_llm = LLMClient(model=redteam_llm_model, api_key=redteam_llm_api_key)
    eval_llm: LLMClient | None = None
    if eval_llm_model:
        eval_llm = LLMClient(model=eval_llm_model, api_key=eval_llm_api_key)

    orchestrator = RedteamOrchestrator(
        sbom=sbom_doc,  # type: ignore[arg-type]
        target_url=target_url,
        policy=cognitive_policy,  # type: ignore[arg-type]
        canary_config=canary_config,  # type: ignore[arg-type]
        profile=profile,
        min_impact_score=min_impact_score,
        chat_path=chat_path,
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
        guided_conversations=guided_conversations,
        guided_max_turns=guided_max_turns,
        guided_concurrency=guided_concurrency,
        extra_headers=extra_headers,
        strict_outcome=strict_outcome,
        redteam_llm=redteam_llm,
        eval_llm=eval_llm,
    )
    findings = await orchestrator.run()

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

    return findings


def _print_findings(findings: list, format: str) -> None:
    """Print findings to stdout in the requested format."""
    from nuguard.models.finding import Severity

    if format == "json":
        typer.echo(
            json.dumps([f.model_dump() for f in findings], indent=2, default=str)
        )
        return

    if not findings:
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
