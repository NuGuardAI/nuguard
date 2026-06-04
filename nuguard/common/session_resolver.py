"""Shared target-session resolution for behavior and redteam modes.

:func:`resolve_target_session` is the single entry-point that both
:class:`~nuguard.behavior.runner.BehaviorRunner` and
:class:`~nuguard.redteam.executor.orchestrator.RedteamOrchestrator` use to
set up the target connection before any scenarios run.  It composes the
existing building-block functions (URL resolution, auth upgrade, bootstrap,
endpoint discovery) and adds two new layers:

1. **Login-response extras** — identity/session fields extracted from the
   login response body (e.g. ``user_id``, ``session_id``) are merged into
   ``chat_payload_extras`` at the lowest precedence so explicit config wins.

2. **SBOM context hints** — ``API_ENDPOINT`` nodes that declare
   ``context_payload_fields`` in their metadata surface missing identity
   fields as config notes, and auto-generate UUIDs for session fields.

The result is a :class:`TargetSessionConfig` dataclass that callers can
hand directly to :func:`~nuguard.common.target_client_builder.build_target_app_client`.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig, AuthSession
    from nuguard.common.bootstrap import TargetHealthReport
    from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TargetSessionConfig
# ---------------------------------------------------------------------------

@dataclass
class TargetSessionConfig:
    """Fully-resolved target connection configuration.

    Produced by :func:`resolve_target_session` and consumed by
    :func:`~nuguard.common.target_client_builder.build_target_app_client`.
    """

    base_url: str
    chat_path: str
    chat_payload_key: str
    chat_payload_list: bool
    chat_payload_extras: dict[str, Any]  # static config + login-response + SBOM hints
    chat_response_key: str | None
    auth_session: "AuthSession"
    resolution_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge_login_response_extras(
    session: "AuthSession",
    config_extras: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Merge identity/session fields from the login response into *config_extras*.

    Returns ``(merged_extras, notes)`` where *notes* describes what was
    auto-injected.  Explicit config always wins — login-response values only
    fill in fields that are NOT already set.
    """
    login_extras = session.login_response_extras()
    if not login_extras:
        return dict(config_extras), []

    merged: dict[str, Any] = dict(login_extras)  # lowest precedence
    merged.update(config_extras)               # config wins

    notes = [
        f"auto-injected '{k}' from login response into chat_payload_extras"
        for k in login_extras
        if k not in config_extras
    ]
    return merged, notes


def _get_sbom_context_fields(
    sbom: "AiSbomDocument",
    chat_path: str,
) -> dict[str, str]:
    """Return the ``context_payload_fields`` from the SBOM node matching *chat_path*.

    Returns an empty dict when no matching node is found or the SBOM has no
    ``context_payload_fields`` metadata.
    """
    try:
        from nuguard.sbom.models import NodeType  # noqa: PLC0415
    except Exception:
        return {}

    best: dict[str, str] = {}
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta or not meta.context_payload_fields:
            continue
        ep = (meta.endpoint or "").strip()
        if ep and ep == chat_path.strip():
            best = dict(meta.context_payload_fields)
            break
        # Fallback: use the first node with context fields when path doesn't match exactly
        if not best:
            best = dict(meta.context_payload_fields)

    return best


def apply_sbom_context_hints(
    sbom: "AiSbomDocument",
    chat_path: str,
    current_extras: dict[str, Any],
    login_extras: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """Apply SBOM-detected context field hints to *current_extras*.

    For each field in the SBOM's ``context_payload_fields`` that is NOT
    already set in *current_extras*:

    - **identity** fields: injected from *login_extras* when available; otherwise
      a config note is emitted telling the user to set the value explicitly.
    - **session** fields: a fresh UUID is generated for each run (the
      :class:`~nuguard.redteam.target.client.TargetAppClient` already extracts
      and forwards session IDs from responses on subsequent turns, so this only
      seeds the first request).

    Returns ``(merged_extras, notes)``.
    """
    hints = _get_sbom_context_fields(sbom, chat_path)
    if not hints:
        return dict(current_extras), []

    merged = dict(current_extras)
    notes: list[str] = []

    for field_name, kind in hints.items():
        if field_name in merged:
            continue  # explicit config always wins

        if kind == "identity":
            if field_name in login_extras:
                merged[field_name] = login_extras[field_name]
                notes.append(
                    f"auto-injected '{field_name}' (identity) from login response"
                )
            else:
                notes.append(
                    f"Endpoint '{chat_path}' declares '{field_name}' (identity field) in its "
                    f"request body but no value was found in the login response or config. "
                    f"Set redteam.chat_payload_extras.{field_name}=<test-user-id> "
                    f"or configure a login_flow that returns '{field_name}'."
                )

        elif kind == "session":
            generated = str(uuid.uuid4())
            merged[field_name] = generated
            notes.append(
                f"auto-generated UUID for '{field_name}' (session field) — "
                f"will be forwarded by TargetAppClient on subsequent turns"
            )

    return merged, notes


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------

async def resolve_target_session(
    target_url: str,
    sbom: "AiSbomDocument",
    auth_config: "AuthConfig | None",
    extra_headers: dict[str, str],
    chat_path: str,
    chat_payload_key: str,
    chat_payload_list: bool,
    chat_payload_extras: dict[str, Any],
    chat_response_key: str | None,
    framework_adapter: Any = None,
    warmup_requests: int = 0,
    canary_config: Any = None,
    probe_payload_extras: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> tuple[TargetSessionConfig, "TargetHealthReport"]:
    """Resolve all target-connection config and return a :class:`TargetSessionConfig`.

    This is the single shared bootstrap path for both behavior and redteam modes.
    It runs the following pipeline in order:

    1. Static-hosting URL resolution (falls back to SBOM ``deployment_urls``)
    2. Basic → login_flow auth upgrade when SBOM has a login endpoint
    3. Auth bootstrap (live login + credential probe)
    4. Login-response extras merge (auto-inject ``user_id`` etc.)
    5. SBOM context-field hints (identity auto-inject or config note; session UUID)
    6. SBOM static endpoint discovery (zero I/O)
    7. Live endpoint probe with extras (only when ``chat_path`` is still empty)
    8. Endpoint quality check (warns when probe returns anonymous/empty session)
    9. Pre-run warmup requests (for serverless/scale-to-zero targets)

    Args:
        target_url: Base URL of the target application (may be a static-hosting URL).
        sbom: Parsed AI-SBOM used for endpoint and auth discovery.
        auth_config: Structured auth config; ``None`` falls back to *extra_headers*.
        extra_headers: Static headers (legacy auth or custom).  Used when *auth_config*
            is ``None`` or ``type='none'``.
        chat_path: Chat endpoint path from config; empty = auto-discover.
        chat_payload_key: JSON body key for the message (default ``"message"``).
        chat_payload_list: Whether to wrap the message value in a list.
        chat_payload_extras: Static extra body fields from config (highest precedence).
        chat_response_key: Explicit response extraction key; ``None`` = auto-detect.
        framework_adapter: Pre-built framework adapter (ADK/CES); ``None`` = auto-detect.
        warmup_requests: Number of lightweight warmup requests to send before scenarios.
        canary_config: Optional canary configuration forwarded to bootstrap.
        probe_payload_extras: Extra fields to include when live-probing endpoints.
            Defaults to *chat_payload_extras* when ``None``.
        run_id: Optional UUID string for the bootstrap health check; auto-generated
            when ``None``.

    Returns:
        ``(TargetSessionConfig, TargetHealthReport)``
    """
    import uuid as _uuid  # noqa: PLC0415

    from nuguard.common.auth_runtime import (  # noqa: PLC0415
        bootstrap_auth_runtime,
        resolve_auth_runtime,
    )
    from nuguard.common.endpoint_probe import (  # noqa: PLC0415
        discover_chat_config_from_sbom,
        is_empty_session_response,
        probe_chat_endpoints,
    )
    from nuguard.common.target_client_builder import (  # noqa: PLC0415
        resolve_auth_config_with_sbom_fallback,
        resolve_target_url,
    )

    resolution_notes: list[str] = []
    _run_id = run_id or str(_uuid.uuid4())

    # ── 1. URL resolution ────────────────────────────────────────────────────
    resolved_url, url_notes = resolve_target_url(target_url, sbom)
    if url_notes:
        resolution_notes.extend(url_notes)
    if resolved_url:
        target_url = resolved_url

    # ── 2. Auth upgrade: basic → login_flow ──────────────────────────────────
    effective_auth = auth_config
    if (
        effective_auth is not None
        and effective_auth.type == "basic"
        and (effective_auth.login_flow is None)
    ):
        effective_auth, auth_note = resolve_auth_config_with_sbom_fallback(effective_auth, sbom)
        if auth_note:
            resolution_notes.append(auth_note)

    auth_runtime = resolve_auth_runtime(
        auth_config=effective_auth,
        headers_override=extra_headers if effective_auth is None else None,
    )

    # ── 3. Auth bootstrap ────────────────────────────────────────────────────
    _probe_extras = probe_payload_extras if probe_payload_extras is not None else chat_payload_extras
    bootstrapper, health_report = await bootstrap_auth_runtime(
        target_url=target_url,
        endpoint=chat_path or "/chat",
        auth_config=auth_runtime.auth_config,
        canary_config=canary_config,
        run_id=_run_id,
        probe_payload_extras=_probe_extras or None,
    )
    bootstrap_headers = bootstrapper.session.headers()
    effective_headers = dict(extra_headers)
    if bootstrap_headers:
        effective_headers.update(bootstrap_headers)

    # ── 4. Login-response extras ──────────────────────────────────────────────
    merged_extras, login_notes = _merge_login_response_extras(
        bootstrapper.session, chat_payload_extras
    )
    resolution_notes.extend(login_notes)
    login_extras = bootstrapper.session.login_response_extras()

    # ── 5. SBOM context hints ─────────────────────────────────────────────────
    merged_extras, hint_notes = apply_sbom_context_hints(
        sbom, chat_path, merged_extras, login_extras
    )
    resolution_notes.extend(hint_notes)

    # ── 6. SBOM static endpoint discovery ────────────────────────────────────
    if not chat_path:
        discovered_path, discovered_key, discovered_list, discovered_resp_key = (
            discover_chat_config_from_sbom(
                sbom,
                chat_path=chat_path,
                chat_payload_key=chat_payload_key,
                chat_payload_list=chat_payload_list,
            )
        )
        if discovered_path and discovered_path != "/chat":
            chat_path = discovered_path
            _log.info("resolve_target_session: SBOM discovered endpoint %s", chat_path)
        if discovered_key and discovered_key != chat_payload_key:
            chat_payload_key = discovered_key
            chat_payload_list = discovered_list
        if discovered_resp_key and not chat_response_key:
            chat_response_key = discovered_resp_key

    # ── 7. Live probe (only when path still unknown) ──────────────────────────
    if not chat_path:
        probe_result = await probe_chat_endpoints(
            target_url=target_url,
            sbom=sbom,
            auth_headers=effective_headers or None,
            known_payload_key=chat_payload_key if chat_payload_key != "message" else None,
            known_payload_list=chat_payload_list,
            probe_payload_extras=_probe_extras or None,
        )
        if probe_result is not None:
            chat_path, chat_payload_key, chat_payload_list = probe_result
            _log.info("resolve_target_session: live probe selected endpoint %s", chat_path)

    # ── 8. Quality check ──────────────────────────────────────────────────────
    # The bootstrap already sent a probe; inspect its response for anonymous-session markers.
    _bootstrap_check = health_report.checks[0] if health_report.checks else None
    if _bootstrap_check and _bootstrap_check.response_text:
        if is_empty_session_response(_bootstrap_check.response_text):
            resolution_notes.append(
                "Endpoint probe returned an empty/anonymous user session "
                "(zero balances, missing identity, or UNKNOWN account ID). "
                "If this app requires a body field (e.g. user_id) to identify the user, "
                "set it in redteam.chat_payload_extras or configure a login_flow "
                "that returns the field."
            )

    # ── 9. Pre-run warmup ─────────────────────────────────────────────────────
    if warmup_requests > 0:
        from nuguard.common.target_client_builder import build_target_app_client  # noqa: PLC0415
        _wu_client = build_target_app_client(
            target_url=target_url,
            endpoint=chat_path or "/chat",
            payload_key=chat_payload_key,
            payload_list=chat_payload_list,
            timeout=30.0,
            auth_headers=effective_headers or None,
            sbom=sbom,
            payload_extras=merged_extras or None,
        )
        async with _wu_client:
            from nuguard.redteam.target.session import AttackSession as _WS  # noqa: PLC0415
            _wu_session = _WS(
                session_id="pre-run-warmup",
                target_url=target_url,
                chain_id="pre-run-warmup",
            )
            for _i in range(warmup_requests):
                try:
                    _resp_text, _ = await _wu_client.send("Hello", _wu_session)
                    _log.info(
                        "resolve_target_session: warmup %d/%d: %s",
                        _i + 1, warmup_requests, (_resp_text or "")[:80],
                    )
                except Exception as _exc:
                    _log.warning(
                        "resolve_target_session: warmup %d/%d failed (non-fatal): %s",
                        _i + 1, warmup_requests, _exc,
                    )

    return (
        TargetSessionConfig(
            base_url=target_url,
            chat_path=chat_path or "/chat",
            chat_payload_key=chat_payload_key,
            chat_payload_list=chat_payload_list,
            chat_payload_extras=merged_extras,
            chat_response_key=chat_response_key,
            auth_session=bootstrapper.session,
            resolution_notes=resolution_notes,
        ),
        health_report,
    )
