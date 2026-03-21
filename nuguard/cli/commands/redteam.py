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
    sbom: Optional[Path] = typer.Option(
        None, "--sbom", help="Path to AI-SBOM JSON file."
    ),
    policy: Optional[Path] = typer.Option(
        None, "--policy", help="Path to Cognitive Policy Markdown file."
    ),
    target: Optional[str] = typer.Option(
        None, "--target", help="URL of the running AI application."
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
) -> None:
    """Run dynamic red-team testing against a live AI application."""
    if ctx.invoked_subcommand is not None:
        return

    # Resolve from nuguard.yaml if not provided on CLI
    from nuguard.config import load_config

    cfg = load_config(None)
    sbom_path = sbom or (Path(cfg.sbom_path) if cfg.sbom_path else None)
    policy_path = policy
    target_url = target or cfg.target_url

    # Validate required inputs
    if not sbom_path or not sbom_path.exists():
        typer.echo(
            "Error: --sbom is required (or set sbom: in nuguard.yaml)", err=True
        )
        raise typer.Exit(code=1)
    if not target_url:
        typer.echo(
            "Error: --target is required (or set target_url: in nuguard.yaml)",
            err=True,
        )
        raise typer.Exit(code=1)

    findings = asyncio.run(
        _run_redteam(
            sbom_path=sbom_path,
            policy_path=policy_path,
            target_url=target_url,
            canary_path=canary,
            profile=profile,
            min_impact_score=min_impact_score,
        )
    )

    # Output
    _print_findings(findings, format)
    if output:
        output.write_text(
            json.dumps([f.model_dump() for f in findings], indent=2, default=str)
        )
        typer.echo(f"Findings written to {output}")

    # Exit code
    _fail_on_severity(findings, fail_on)


async def _run_redteam(
    sbom_path: Path,
    policy_path: Path | None,
    target_url: str,
    canary_path: Path | None,
    profile: str,
    min_impact_score: float,
) -> list:
    """Async inner function to load inputs and run the orchestrator."""
    from nuguard.models.policy import CognitivePolicy
    from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
    from nuguard.redteam.target.canary import CanaryConfig
    from nuguard.sbom.serializer import AiSbomSerializer

    # Load SBOM
    try:
        sbom = AiSbomSerializer.from_json(sbom_path.read_text())
    except Exception as exc:
        typer.echo(f"Error loading SBOM: {exc}", err=True)
        raise typer.Exit(code=1)

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

    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url=target_url,
        policy=cognitive_policy,
        canary_config=canary_config,
        profile=profile,
        min_impact_score=min_impact_score,
    )
    return await orchestrator.run()


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
