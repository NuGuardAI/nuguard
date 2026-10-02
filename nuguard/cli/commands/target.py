"""nuguard target — target connectivity and auth verification commands."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from nuguard.common.auth import AuthConfig
from nuguard.common.auth_runtime import resolve_auth_runtime
from nuguard.common.errors import TargetEndpointNotFoundError, TargetUnavailableError
from nuguard.config import load_config
from nuguard.redteam.target.canary import CanaryConfig

if TYPE_CHECKING:
    from nuguard.common.discovery import DiscoveredProfile
    from nuguard.common.session_resolver import TargetSessionConfig
    from nuguard.sbom.models import AiSbomDocument

target_app = typer.Typer(name="target", help="Target connectivity and auth verification.")
console = Console()

_AUTH_REJECTION_STATUS_CODES = frozenset({401, 403})
_BROWSER_SESSION_REFRESH_COMMAND = "nuguard target discover-browser --write --yes"


def _browser_session_refresh_hint(
    *,
    status_code: int | None,
    identity: str,
    auth_type: str,
) -> str | None:
    if (
        identity == "default"
        and auth_type == "cookie_file"
        and status_code in _AUTH_REJECTION_STATUS_CODES
    ):
        return (
            "The saved browser session may have expired. "
            f"Refresh it with: {_BROWSER_SESSION_REFRESH_COMMAND}"
        )
    return None


@target_app.command(name="verify")
def verify_command(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to nuguard.yaml"),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--target", help="Target base URL (overrides nuguard.yaml)"),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Chat endpoint path (overrides nuguard.yaml)"),
    ] = None,
    auth_header: Annotated[
        str | None,
        typer.Option("--auth-header", help="Auth header string e.g. 'Authorization: Bearer tok'"),
    ] = None,
    canary: Annotated[
        Path | None,
        typer.Option("--canary", help="Path to canary.json (verifies tenant tokens too)"),
    ] = None,
    sbom: Annotated[
        Path | None,
        typer.Option(
            "--sbom",
            help=(
                "Path to AI-SBOM JSON (overrides nuguard.yaml). When present, enables "
                "the same chat-endpoint auto-discovery and pre-scan account/golden-data "
                "discovery used by 'nuguard behavior' and 'nuguard redteam'."
            ),
        ),
    ] = None,
    discovery_max_turns: Annotated[
        int | None,
        typer.Option(
            "--discovery-max-turns",
            help="Max pre-scan discovery turns (default: redteam.discovery_max_turns, or 3).",
        ),
    ] = None,
    preflight_candidates: Annotated[
        int | None,
        typer.Option(
            "--preflight-candidates",
            help=(
                "How many alternative SBOM candidates to test when the resolved chat "
                "endpoint's reply isn't clearly conversational (default: "
                "redteam.preflight_candidates, or 3) — matches the pool a subsequent "
                "Behavior/Redteam run would test."
            ),
        ),
    ] = None,
    skip_discovery: Annotated[
        bool,
        typer.Option(
            "--skip-discovery/--no-skip-discovery",
            help="Skip the pre-scan account/golden-data discovery conversation.",
        ),
    ] = False,
) -> None:
    """Verify authentication and connectivity against the target AI application.

    Sends a single probe request for each declared credential (default + canary tenants)
    and reports HTTP status, response time, and auth result. Exits non-zero if any
    non-skipped credential fails.

    When an AI-SBOM is available (via --sbom or nuguard.yaml's ``sbom:``), this command
    also auto-discovers the live chat endpoint from the SBOM (same logic as 'behavior'
    and 'redteam') and runs pre-scan discovery: a short conversation with the agent that
    extracts the authenticated user's real account/name and reference IDs, so you can
    confirm *which* user/account NuGuard is scanning before running a full scan.

    Reads redteam.target, redteam.auth, and sbom from nuguard.yaml.

    Examples:
        nuguard target verify
        nuguard target verify --target http://localhost:3000 --auth-header "Authorization: Bearer $TOKEN"
        nuguard target verify --config nuguard.yaml --sbom app.sbom.json --canary canary.json
    """
    asyncio.run(
        _verify_async(
            config,
            target,
            endpoint,
            auth_header,
            canary,
            sbom,
            discovery_max_turns,
            skip_discovery,
            preflight_candidates,
        )
    )


async def _verify_async(
    config_path: Path | None,
    target_override: str | None,
    endpoint_override: str | None,
    auth_header_override: str | None,
    canary_path: Path | None,
    sbom_override: Path | None = None,
    discovery_max_turns_override: int | None = None,
    skip_discovery_override: bool = False,
    preflight_candidates_override: int | None = None,
) -> None:
    # Load config
    try:
        cfg = load_config(config_path)
    except Exception as exc:
        console.print(f"[red]Error:[/red] failed to load config: {exc}")
        raise typer.Exit(code=1) from exc

    # Resolve target URL and auth from redteam config
    target_url: str | None = target_override or cfg.target_url
    # Empty string (not "/chat") means "not explicitly set" — lets SBOM
    # auto-discovery pick the real endpoint, matching behavior/redteam semantics.
    ep_configured = endpoint_override or getattr(cfg, "target_endpoint", "") or ""
    if auth_header_override:
        auth = AuthConfig.from_header_string(auth_header_override)
        auth_runtime = resolve_auth_runtime(auth_config=auth)
    else:
        auth_runtime = resolve_auth_runtime(
            auth_config=cfg.resolved_auth_config(),
            headers_override=cfg.redteam_headers,
        )

    auth = auth_runtime.auth_config

    if not target_url:
        console.print(
            "[red]Error:[/red] No target URL. Set redteam.target in nuguard.yaml or pass --target."
        )
        raise typer.Exit(code=1)

    # Load canary (for tenant token verification)
    # Prefer explicit --canary flag, then top-level canary_path.
    canary_config: CanaryConfig | None = None
    if canary_path:
        canary_file: Path | None = canary_path
    elif cfg.canary_path:
        canary_file = Path(cfg.canary_path)
    else:
        canary_file = None

    if canary_file and canary_file.exists():
        canary_config = CanaryConfig.load(canary_file)
    elif canary_file:
        console.print(f"[yellow]Warning:[/yellow] canary file not found: {canary_file}")

    # Load SBOM (optional) — enables endpoint auto-discovery + pre-scan discovery
    _sbom_path_val = getattr(cfg, "sbom_path", None)
    sbom_path = sbom_override or (Path(_sbom_path_val) if _sbom_path_val else None)
    sbom_doc: "AiSbomDocument | None" = None
    if sbom_path and sbom_path.exists():
        from nuguard.sbom.serializer import AiSbomSerializer

        try:
            sbom_doc = AiSbomSerializer.from_json(sbom_path.read_text())
        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] failed to load SBOM {sbom_path}: {exc}")
    elif sbom_path:
        console.print(f"[yellow]Warning:[/yellow] SBOM file not found: {sbom_path}")

    # Load the already-enriched SBOM artifact when present/fresh (same
    # structural-enrichment pipeline behavior/redteam already use) — without
    # this, target verify always reads the pristine sbom_path directly and
    # can never see its own (or behavior/redteam's) previously-persisted
    # discovered_profile / resolved_chat_endpoint cache data, making the
    # issue #611 Phase 2/3 caching unreachable for this command in either
    # direction. See nuguard.cli.common.enrich_sbom_for_run.
    if sbom_doc is not None:
        from nuguard.cli.common import enrich_sbom_for_run

        sbom_doc = await enrich_sbom_for_run(
            sbom=sbom_doc,
            sbom_path=sbom_path,
            target_url=target_url,
            llm_enabled=getattr(cfg, "use_llm", False),
            llm_model=cfg.litellm_model,
            llm_api_key=cfg.litellm_api_key,
            llm_api_base=cfg.litellm_api_base,
            probe_auth_header=auth.header if auth.type != "none" else None,
            log_prefix="target verify",
        )
        # enrich_sbom_for_run() may return a cached artifact loaded from disk,
        # not guaranteed to have gone through topology enrichment — re-run it
        # here (idempotent) so endpoint-candidate ranking/discovery below
        # always sees the derived risk attributes (mirrors redteam.py).
        from nuguard.sbom.enricher import enrich as _enrich_topology_post

        _enrich_topology_post(sbom_doc)

    effective_discovery_max_turns = (
        discovery_max_turns_override
        if discovery_max_turns_override is not None
        else getattr(cfg, "redteam_discovery_max_turns", 3)
    )
    effective_preflight_candidates = (
        preflight_candidates_override
        if preflight_candidates_override is not None
        else getattr(cfg, "redteam_preflight_candidates", 3)
    )
    effective_skip_discovery = skip_discovery_override or getattr(
        cfg, "redteam_skip_discovery", False
    )

    console.print("\n[bold]NuGuard Target Verify[/bold]")
    console.print(f"  Target:   {target_url}")
    console.print(f"  Auth:     {auth.type}")
    if canary_config:
        tenant_count = len([t for t in canary_config.tenants if t.session_token])
        console.print(f"  Tenants:  {tenant_count} with session_token in canary.json")
    console.print()

    session_cfg: "TargetSessionConfig | None" = None
    chat_payload_key = getattr(cfg, "redteam_chat_payload_key", "message")
    chat_payload_list = getattr(cfg, "redteam_chat_payload_list", False)
    chat_response_key = getattr(cfg, "redteam_chat_response_key", "") or None
    chat_payload_extras = getattr(cfg, "redteam_chat_payload_extras", None) or {}
    configured_fields: set[str] = getattr(cfg, "model_fields_set", set())

    if sbom_doc is not None:
        from nuguard.common.endpoint_detection import UNSET, resolve_chat_endpoint
        from nuguard.common.endpoint_detection.context import resolve_api_origin
        from nuguard.common.session_resolver import resolve_target_session

        # Resolve the chat endpoint *before* auth bootstrap — bootstrap must probe
        # the real chat endpoint, not the generic "/chat" default. This mirrors
        # RedteamOrchestrator's constructor (zero-I/O SBOM discovery) + its
        # _maybe_probe_endpoints() (live probe fallback), both of which run before
        # bootstrap in the real scan.
        resolved_target_url, url_notes = await resolve_api_origin(target_url, sbom_doc)
        pre_resolution_notes: list[str] = list(url_notes)
        if resolved_target_url:
            target_url = resolved_target_url

        resolved_endpoint = await resolve_chat_endpoint(
            target_url=target_url,
            sbom=sbom_doc,
            endpoint=ep_configured if ep_configured else UNSET,
            payload_key=(
                chat_payload_key
                if "redteam_chat_payload_key" in configured_fields
                else UNSET
            ),
            payload_list=(
                chat_payload_list
                if "redteam_chat_payload_list" in configured_fields
                else UNSET
            ),
            response_key=(
                chat_response_key
                if "redteam_chat_response_key" in configured_fields
                else UNSET
            ),
            auth_headers=auth_runtime.initial_headers or None,
            probe_payload_extras=chat_payload_extras or None,
        )
        discovered_path = resolved_endpoint.path or "/chat"
        discovered_key = resolved_endpoint.payload_key or "message"
        discovered_list = resolved_endpoint.payload_list
        chat_response_key = resolved_endpoint.response_key
        pre_resolution_notes.extend(resolved_endpoint.notes)
        # Keep a live-probe-confirmed endpoint instead of re-resolving it below.
        keep_resolved = not ep_configured and resolved_endpoint.path_source.value == "probe"

        try:
            session_cfg, report = await resolve_target_session(
                target_url=target_url,
                sbom=sbom_doc,
                auth_config=auth,
                extra_headers=auth_runtime.initial_headers,
                chat_path=discovered_path,
                chat_payload_key=discovered_key,
                chat_payload_list=discovered_list,
                chat_payload_extras=chat_payload_extras,
                chat_response_key=chat_response_key,
                canary_config=canary_config,
                config_path=config_path,
                endpoint_explicit=bool(ep_configured) or keep_resolved,
                payload_key_explicit=keep_resolved or "redteam_chat_payload_key" in configured_fields,
                response_key_explicit="redteam_chat_response_key" in configured_fields,
                endpoint_source_hint="probe" if keep_resolved else None,
            )
        except TargetEndpointNotFoundError as exc:
            console.print(f"[red]✗ Endpoint discovery failed:[/red] {exc}")
            raise typer.Exit(code=1)
        except TargetUnavailableError as exc:
            console.print(f"[red]✗ Target unavailable:[/red] {exc}")
            raise typer.Exit(code=2)

        if pre_resolution_notes:
            session_cfg.resolution_notes = pre_resolution_notes + session_cfg.resolution_notes

        console.print("[bold]API Endpoint[/bold]")
        console.print(f"  Path:          {session_cfg.chat_path}")
        console.print(f"  Payload key:   {session_cfg.chat_payload_key!r}")
        console.print(f"  Response key:  {session_cfg.chat_response_key!r}")
        if session_cfg.resolution_notes:
            console.print("  Notes:")
            for note in session_cfg.resolution_notes:
                console.print(f"    - {note}")
        console.print()
    else:
        # No SBOM — still route through the shared resolver (issue #532) so
        # an unconfigured endpoint gets the same live-probe fallback-path
        # discovery behavior/redteam use, instead of silently defaulting to
        # "/chat" and reporting a resulting 404 as "verified". Account/
        # golden-data pre-scan discovery below stays SBOM-gated — that's a
        # separate capability from endpoint discovery.
        from nuguard.common.session_resolver import resolve_target_session

        console.print("[bold]API Endpoint[/bold]")
        console.print(
            "  [dim]No SBOM available — account/golden-data discovery skipped. "
            "Pass --sbom (or set sbom: in nuguard.yaml) to enable.[/dim]"
        )

        try:
            session_cfg, report = await resolve_target_session(
                target_url=target_url,
                sbom=None,
                auth_config=auth,
                extra_headers=auth_runtime.initial_headers,
                chat_path=ep_configured,
                chat_payload_key=chat_payload_key,
                chat_payload_list=chat_payload_list,
                chat_payload_extras=chat_payload_extras,
                chat_response_key=chat_response_key,
                canary_config=canary_config,
                config_path=config_path,
                endpoint_explicit=bool(ep_configured),
                payload_key_explicit="redteam_chat_payload_key" in configured_fields,
                response_key_explicit="redteam_chat_response_key" in configured_fields,
            )
        except TargetEndpointNotFoundError as exc:
            console.print(f"[red]✗ Endpoint discovery failed:[/red] {exc}")
            raise typer.Exit(code=1)
        except TargetUnavailableError as exc:
            console.print(f"[red]✗ Target unavailable:[/red] {exc}")
            raise typer.Exit(code=2)

        console.print(f"  Path:          {session_cfg.chat_path}")
        console.print(f"  Payload key:   {session_cfg.chat_payload_key!r}")
        console.print(f"  Response key:  {session_cfg.chat_response_key!r}")
        if session_cfg.resolution_notes:
            console.print("  Notes:")
            for note in session_cfg.resolution_notes:
                console.print(f"    - {note}")
        console.print()

    # Pre-scan discovery: connect as the authenticated user and extract their
    # real account/name and reference IDs — same conversation used by 'behavior'
    # and 'redteam' before scenario generation. Runs *before* the table so the
    # results can be published in the Identity/Detail columns below.
    profile: "DiscoveredProfile | None" = None
    discovery_skip_note: str | None = None
    if sbom_doc is not None and session_cfg is not None and not effective_skip_discovery:
        default_check = report.checks[0] if report.checks else None
        if default_check is not None and default_check.status != "ok":
            discovery_skip_note = "default credential did not verify"
        else:
            profile, preflight_skip_note = await _run_pre_scan_discovery(
                sbom_doc,
                session_cfg,
                max_turns=effective_discovery_max_turns,
                has_explicit_endpoint=bool(ep_configured),
                preflight_candidates=effective_preflight_candidates,
                sbom_path=sbom_path,
            )
            if preflight_skip_note:
                discovery_skip_note = preflight_skip_note

    # Render results table — Identity/Detail carry the discovered user/account
    # and golden data for the default identity's row.
    table = Table(show_header=True, header_style="bold")
    table.add_column("Identity", style="cyan")
    table.add_column("Auth Type")
    table.add_column("Status")
    table.add_column("HTTP", justify="right")
    table.add_column("Time (ms)", justify="right")
    table.add_column("Detail")

    status_styles = {
        "ok": "green",
        "auth_failed": "red",
        "target_unavailable": "red",
        "endpoint_not_found": "red",
        "skipped": "dim",
    }

    user_ref = (
        _configured_user_ref(auth, session_cfg.chat_payload_extras)
        if session_cfg is not None
        else ""
    )

    for check in report.checks:
        style = status_styles.get(check.status, "white")
        identity_cell = check.identity
        detail_cell = check.error_detail[:60] if check.error_detail else ""
        if not detail_cell and check.body_warning:
            detail_cell = f"[yellow]⚠ {check.body_warning[:60]}[/yellow]"
        if not detail_cell and check.payload_hint:
            detail_cell = f"[yellow]⚠ {check.payload_hint[:60]}[/yellow]"

        if check.identity == "default":
            identity_extra = ""
            if user_ref and profile is not None and profile.customer_name:
                identity_extra = f"{user_ref} · {profile.customer_name}"
            elif profile is not None and profile.customer_name:
                identity_extra = profile.customer_name
            elif user_ref:
                identity_extra = user_ref
            if identity_extra:
                identity_cell = f"{identity_cell}\n[dim]{identity_extra}[/dim]"

            if not detail_cell and profile is not None and not profile.is_empty:
                detail_cell = _golden_data_summary(profile)

        table.add_row(
            identity_cell,
            check.auth_type,
            f"[{style}]{check.status}[/{style}]",
            str(check.http_status_code) if check.http_status_code else "—",
            f"{check.response_time_ms:.0f}" if check.response_time_ms else "—",
            detail_cell,
        )

    console.print(table)

    if discovery_skip_note:
        console.print(
            f"\n[dim]Skipping account/golden-data discovery — {discovery_skip_note}.[/dim]"
        )
    elif sbom_doc is not None and not effective_skip_discovery:
        _print_discovery_footnote(profile)

    if report.all_ok:
        console.print("\n[green]All credentials verified successfully.[/green]")
        raise typer.Exit(code=0)
    else:
        failed = report.failed_checks
        console.print(f"\n[red]{len(failed)} credential(s) failed verification.[/red]")
        for f in failed:
            if f.status == "auth_failed":
                console.print(
                    f"  → [cyan]{f.identity}[/cyan]: authentication rejected "
                    f"(HTTP {f.http_status_code}). Check credentials."
                )
                hint = _browser_session_refresh_hint(
                    status_code=f.http_status_code,
                    identity=f.identity,
                    auth_type=auth.type,
                )
                if hint is not None:
                    console.print(f"   [yellow]{hint}[/yellow]")
            elif f.status == "endpoint_not_found":
                if f.http_status_code == 405:
                    console.print(
                        f"  → [cyan]{f.identity}[/cyan]: endpoint exists at {f.endpoint} but "
                        f"rejected the HTTP method NuGuard uses for chat requests (POST). "
                        f"This route may not be the right chat endpoint — check "
                        f"target_endpoint in nuguard.yaml."
                    )
                else:
                    console.print(
                        f"  → [cyan]{f.identity}[/cyan]: endpoint not found (HTTP 404) at "
                        f"{f.endpoint}. Set --endpoint, target_endpoint in nuguard.yaml, "
                        f"or pass --sbom to enable auto-discovery."
                    )
            else:
                console.print(
                    f"  → [cyan]{f.identity}[/cyan]: target unreachable. "
                    f"Is the app running at {target_url}?"
                )
        raise typer.Exit(code=1)


async def _run_pre_scan_discovery(
    sbom_doc: "AiSbomDocument",
    session_cfg: "TargetSessionConfig",
    *,
    max_turns: int,
    has_explicit_endpoint: bool,
    preflight_candidates: int,
    sbom_path: "Path | None" = None,
) -> "tuple[DiscoveredProfile | None, str | None]":
    """Run the same pre-scan discovery conversation used by behavior/redteam.

    Returns ``(profile, skip_reason)``. ``profile`` is ``None`` (rather than
    raising) on failure — discovery is diagnostic, never fatal to
    `target verify`. ``skip_reason`` is set only when a working chat endpoint
    could not be validated at all (as opposed to discovery running but
    finding nothing), so the caller can surface it the same way as the
    existing "default credential did not verify" skip note, instead of a
    differently-styled message printed from inside this function.
    """
    from nuguard.common.discovery import (
        cached_discovery_profile,
        profile_cache_fingerprint,
        run_discovery_conversation,
    )
    from nuguard.common.endpoint_preflight import (
        cached_endpoint_resolution,
        persist_endpoint_resolution,
        validate_and_rotate_chat_endpoint,
    )
    from nuguard.common.target_client_builder import build_target_app_client
    from nuguard.redteam.target.session import AttackSession

    try:
        client = build_target_app_client(
            target_url=session_cfg.base_url,
            endpoint=session_cfg.chat_path,
            payload_key=session_cfg.chat_payload_key,
            payload_list=session_cfg.chat_payload_list,
            response_key=session_cfg.chat_response_key,
            auth_headers=session_cfg.effective_headers or None,
            sbom=sbom_doc,
            payload_extras=session_cfg.chat_payload_extras or None,
        )
        session = AttackSession(
            session_id="target-verify-discovery",
            target_url=session_cfg.base_url,
            chain_id="target-verify-discovery",
        )
        use_case = ""
        if sbom_doc.summary is not None:
            use_case = getattr(sbom_doc.summary, "use_case", "") or ""
        auth_config = getattr(session_cfg.auth_session, "auth_config", None)
        async with client:
            # Validate (and if needed rotate) the chat endpoint, and bootstrap
            # any templated path param (e.g. :id on a create-conversation-then-
            # post-message route) before sending any discovery turn — the same
            # preflight RedteamOrchestrator.run() and BehaviorRunner already run.
            # Without this, every turn against a templated endpoint fails with
            # "[CONFIG_ERROR: unresolved path param ...]" — see issue #611.
            #
            # First check for a previously-validated resolution cached on the
            # SBOM by an earlier Target Verify/Behavior/Redteam run against
            # this same target/auth — reusing it skips the live round-trip
            # entirely. A cache entry for a DIFFERENT path than the one
            # explicitly configured here is never used (config-wins
            # precedence; see endpoint-resolution-precedence-plan.md).
            sbom_hit = cached_endpoint_resolution(
                sbom_doc,
                session_cfg.base_url,
                auth_config,
                required_chat_path=session_cfg.chat_path if has_explicit_endpoint else None,
            )
            if sbom_hit is not None:
                resolved, resolved_params = sbom_hit
                client.set_chat_endpoint(
                    resolved.chat_path, resolved.chat_payload_key,
                    resolved.chat_payload_list, resolved.chat_response_key,
                )
                for pp_name, pp_value in resolved_params.items():
                    client.set_path_param(pp_name, pp_value)
                console.print(
                    f"  [dim]Chat endpoint {resolved.chat_path!r} reused from a "
                    "previously-validated SBOM resolution — skipped live preflight.[/dim]"
                )
            else:
                preflight = await validate_and_rotate_chat_endpoint(
                    client,
                    sbom_doc,
                    has_explicit_endpoint=has_explicit_endpoint,
                    target_url=session_cfg.base_url,
                    auth_headers=session_cfg.effective_headers or None,
                    max_candidates=preflight_candidates,
                )
                for note in preflight.notes:
                    console.print(f"  [dim]{note}[/dim]")
                if not preflight.ok:
                    return None, "no working chat endpoint found during preflight validation"
                # Prefer the rotation outcome itself over re-reading
                # client.chat_path: self-contained rather than relying on
                # validate_and_rotate_chat_endpoint having mutated *client*
                # as a side effect.
                if preflight.rotated_endpoint is not None:
                    final_path, final_payload_key, final_payload_list, final_response_key = (
                        preflight.rotated_endpoint
                    )
                else:
                    final_path = session_cfg.chat_path
                    final_payload_key = getattr(client, "_chat_payload_key", "message")
                    final_payload_list = bool(getattr(client, "_chat_payload_list", False))
                    final_response_key = getattr(client, "_chat_response_key", None)
                persist_endpoint_resolution(
                    sbom_doc,
                    session_cfg.base_url,
                    auth_config,
                    chat_path=final_path,
                    chat_payload_key=final_payload_key,
                    chat_payload_list=final_payload_list,
                    chat_response_key=final_response_key,
                    endpoint_source=preflight.endpoint_source,
                    path_param_values=dict(getattr(client, "path_param_values", None) or {}),
                )
                if sbom_path is not None:
                    from nuguard.common.auto_sbom_enricher import (
                        persist_endpoint_resolution_sbom,
                    )

                    try:
                        persist_endpoint_resolution_sbom(sbom_doc, sbom_path)
                    except Exception as exc:
                        console.print(
                            f"  [yellow]Could not persist endpoint resolution (non-fatal):[/yellow] {exc}"
                        )
            # Same cache-then-fall-back pattern as the endpoint resolution
            # above, applied to the discovered identity profile (issue #611
            # Phase 2) — previously wired into verify_target()'s sibling
            # BehaviorRunner/RedteamOrchestrator call sites but never into
            # this CLI's own discovery implementation, so every target
            # verify run re-ran the live DISCOVER conversation regardless of
            # a prior successful discovery against the same target/auth.
            cached_profile = cached_discovery_profile(sbom_doc, session_cfg.base_url, auth_config)
            if cached_profile is not None:
                console.print(
                    f"  [dim]Pre-scan discovery (from enriched SBOM): "
                    f"name={cached_profile.customer_name!r} ids={cached_profile.ids}[/dim]"
                )
                return cached_profile, None

            profile = await run_discovery_conversation(
                client, session, use_case=use_case, max_turns=max_turns
            )
            if not profile.is_empty:
                sbom_doc.discovered_profile = profile.model_dump(mode="json")
                sbom_doc.discovered_profile_fingerprint = profile_cache_fingerprint(
                    session_cfg.base_url, auth_config
                )
                if sbom_path is not None:
                    from nuguard.common.auto_sbom_enricher import (
                        persist_discovery_profile_sbom,
                    )

                    try:
                        persist_discovery_profile_sbom(sbom_doc, sbom_path)
                    except Exception as exc:
                        console.print(
                            f"  [yellow]Could not persist discovery profile (non-fatal):[/yellow] {exc}"
                        )
            return profile, None
    except Exception as exc:
        console.print(f"  [yellow]Discovery failed (non-fatal):[/yellow] {exc}")
        return None, None


# Field names checked (in order) for a configured user/account identity to
# display alongside the discovered name — covers the common shapes apps use
# to carry identity in the chat POST body (e.g. Pinnacle Bank's user_id: alice).
_IDENTITY_EXTRA_KEYS = (
    "user_id",
    "userId",
    "username",
    "user",
    "account_id",
    "customer_id",
    "tenant_id",
)


def _configured_user_ref(auth: AuthConfig, chat_payload_extras: dict[str, object]) -> str:
    """Best-effort user/account reference from config: payload extras, then auth username."""
    for key in _IDENTITY_EXTRA_KEYS:
        value = chat_payload_extras.get(key)
        if value:
            return str(value)
    username = getattr(auth, "username", None)
    return str(username) if username else ""


def _golden_data_summary(profile: "DiscoveredProfile") -> str:
    """One-line golden-data summary (IDs + entities) for the results table's Detail cell."""
    parts: list[str] = []
    if profile.ids:
        parts.append(", ".join(profile.ids))
    if profile.entity_map:
        parts.append("; ".join(f"{k}={v}" for k, v in profile.entity_map.items()))
    return " | ".join(parts)[:80]


def _print_discovery_footnote(profile: "DiscoveredProfile | None") -> None:
    """Print discovery details not already surfaced in the results table."""
    if profile is None:
        return
    if profile.is_empty:
        console.print("\n[dim]No account/user data extracted by pre-scan discovery.[/dim]")
        if profile.capability_hint:
            console.print(f"[dim]Capability hint: {profile.capability_hint[:200]}[/dim]")
    console.print(f"[dim]Discovery turns sent: {profile.turns_sent}[/dim]")
