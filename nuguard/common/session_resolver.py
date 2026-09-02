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
   fields as config notes, and omits session fields so the server creates real sessions.

The result is a :class:`TargetSessionConfig` dataclass that callers can
hand directly to :func:`~nuguard.common.target_client_builder.build_target_app_client`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig, AuthSession
    from nuguard.common.bootstrap import TargetHealthReport
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


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
    # Nested payload value template from OpenAPI schema detection.
    # When set, the value for chat_payload_key is built from this template
    # (e.g. {"role": "user", "content": "..."}) instead of a plain string.
    chat_payload_value_template: "dict[str, object] | None" = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge_login_response_extras(
    session: "AuthSession | None",
    config_extras: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Merge identity/session fields from the login response into *config_extras*.

    Returns ``(merged_extras, notes)`` where *notes* describes what was
    auto-injected.  Explicit config always wins — login-response values only
    fill in fields that are NOT already set.

    ``session`` is ``None`` when bootstrap couldn't establish an auth session
    (e.g. the user configured auth directly on the chat endpoint instead of an
    auth endpoint the SBOM declares) — that's not an error, just nothing to merge.
    """
    if session is None:
        return dict(config_extras), []
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


_IDENTITY_FIELD_NAMES: frozenset[str] = frozenset({
    "user_id", "userid", "uid", "user", "userId",
})


def _get_sbom_context_fields(
    sbom: "AiSbomDocument",
    chat_path: str,
) -> dict[str, str]:
    """Return context payload field hints from the SBOM node matching *chat_path*.

    First looks for explicit ``context_payload_fields`` metadata.  When none are
    declared, falls back to scanning the endpoint's ``request_body_schema`` for
    well-known identity field names (``user_id``, ``userId``, ``uid``) and
    classifies them as ``"identity"`` automatically.

    Returns an empty dict when no matching node is found.
    """
    try:
        from nuguard.sbom.models import NodeType  # noqa: PLC0415
    except Exception:
        return {}

    best_explicit: dict[str, str] = {}
    best_inferred: dict[str, str] = {}

    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta:
            continue
        ep = (meta.endpoint or "").strip()
        path_matches = ep and ep == chat_path.strip()

        # Explicit context_payload_fields always takes priority
        if meta.context_payload_fields:
            if path_matches:
                return dict(meta.context_payload_fields)
            if not best_explicit:
                best_explicit = dict(meta.context_payload_fields)
            continue

        # Infer identity fields from request_body_schema when context_payload_fields absent
        schema = getattr(meta, "request_body_schema", None) or {}
        if isinstance(schema, dict) and schema:
            inferred: dict[str, str] = {
                field: "identity"
                for field in schema
                if field.lower() in _IDENTITY_FIELD_NAMES
            }
            if inferred:
                if path_matches:
                    _log.debug(
                        "_get_sbom_context_fields: inferred identity fields %s from "
                        "request_body_schema of endpoint '%s'",
                        list(inferred), ep or chat_path,
                    )
                    return inferred
                if not best_inferred:
                    best_inferred = inferred

    return best_explicit or best_inferred


def _identity_candidates_from_username(username: str) -> list[str]:
    """Return ordered candidate values to try for an identity body field.

    Priority:
      1. Full username as-is (e.g. ``alice.johnson@pinnaclebank.com``)
      2. Strip ``@domain.tld``          → ``alice.johnson``
      3. Strip ``.`` and everything after the first dot  → ``alice``
    """
    candidates: list[str] = [username]
    if "@" in username:
        local = username.split("@")[0]
        if local and local not in candidates:
            candidates.append(local)
        if "." in local:
            first = local.split(".")[0]
            if first and first not in candidates:
                candidates.append(first)
    return candidates


def apply_sbom_context_hints(
    sbom: "AiSbomDocument",
    chat_path: str,
    current_extras: dict[str, Any],
    login_extras: dict[str, str],
    auth_username: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Apply SBOM-detected context field hints to *current_extras*.

    For each field in the SBOM's ``context_payload_fields`` that is NOT
    already set in *current_extras*:

    - **identity** fields: injected from *login_extras* when available.  When no
      login response contains the value, candidates are derived from *auth_username*
      (full → strip domain → first name) and the first candidate is injected.  The
      full candidate list is stored under ``__<field>_candidates__`` so the caller
      can rotate to the next candidate if the first attempt produces an empty profile.
    - **session** fields: the field is omitted from the first request so the server
      creates a real session. :class:`~nuguard.redteam.target.client.TargetAppClient`
      extracts and forwards the returned session ID on subsequent turns.

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
            elif auth_username:
                candidates = _identity_candidates_from_username(auth_username)
                merged[field_name] = candidates[0]
                # Store remaining candidates for rotation by the discovery loop
                _cand_key = f"__{field_name}_candidates__"
                merged[_cand_key] = candidates
                notes.append(
                    f"auto-derived identity field '{field_name}'={candidates[0]!r} from "
                    f"auth.username (candidates to try: {candidates}). "
                    f"Set chat_payload_extras.{field_name} explicitly to override."
                )
            else:
                notes.append(
                    f"Endpoint '{chat_path}' declares '{field_name}' (identity field) in its "
                    f"request body but no value was found in the login response or config. "
                    f"Set chat_payload_extras.{field_name}=<test-user-id> "
                    f"or configure a login_flow that returns '{field_name}'."
                )

        elif kind == "session":
            # Don't inject a random UUID — omit the field and let the server create
            # the session. TargetAppClient extracts and forwards the real session ID
            # from response bodies on subsequent turns; a fake UUID would cause the
            # agent to reject the session and return empty responses.
            notes.append(
                f"Endpoint '{chat_path}' declares '{field_name}' (session field) — "
                f"omitting from first request so the server creates a real session; "
                f"TargetAppClient will inject the returned value on subsequent turns. "
                f"Set chat_payload_extras.{field_name} explicitly to pre-seed a session ID."
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
    ws_auth_message: dict[str, Any] | None = None,
) -> tuple[TargetSessionConfig, "TargetHealthReport"]:
    """Resolve all target-connection config and return a :class:`TargetSessionConfig`.

    This is the single shared bootstrap path for both behavior and redteam modes.
    It runs the following pipeline in order:

    1. Static-hosting URL resolution (falls back to SBOM ``deployment_urls``)
    2. Basic → login_flow auth upgrade when SBOM has a login endpoint
    3. Auth bootstrap (live login + credential probe) — a WebSocket handshake
       when the target is detected as a WS endpoint, HTTP POST otherwise
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
        ws_auth_message: First-message auth payload for WebSocket targets that
            authenticate over the first frame instead of headers (e.g.
            ``{"type": "auth", "token": "..."}``); ignored for HTTP targets.

    Returns:
        ``(TargetSessionConfig, TargetHealthReport)``
    """
    import uuid as _uuid  # noqa: PLC0415

    from nuguard.common.auth_runtime import (  # noqa: PLC0415
        bootstrap_auth_runtime,
        resolve_auth_runtime,
    )
    from nuguard.common.endpoint_probe import (  # noqa: PLC0415
        discover_chat_candidates_from_sbom,
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
    # WS detection must happen before bootstrap (it decides handshake vs POST),
    # so it runs zero-I/O against whatever the SBOM already knows — the fuller
    # live-probe-based discovery in steps 6/7 below still applies afterwards.
    is_websocket = chat_payload_key == "__websocket__"
    if not is_websocket:
        try:
            ws_candidates = discover_chat_candidates_from_sbom(sbom, chat_path=chat_path)
            if chat_path:
                is_websocket = any(
                    path == chat_path and key == "__websocket__" for path, key, _l, _r in ws_candidates
                )
            else:
                is_websocket = bool(ws_candidates) and ws_candidates[0][1] == "__websocket__"
        except Exception as exc:
            _log.debug("resolve_target_session: WS pre-detection failed: %s", exc)

    _probe_extras = probe_payload_extras if probe_payload_extras is not None else chat_payload_extras
    bootstrapper, health_report = await bootstrap_auth_runtime(
        target_url=target_url,
        endpoint=chat_path or ("/ws" if is_websocket else "/chat"),
        auth_config=auth_runtime.auth_config,
        canary_config=canary_config,
        run_id=_run_id,
        probe_payload_extras=_probe_extras or None,
        is_websocket=is_websocket,
        ws_auth_message=ws_auth_message,
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
    _auth_username = getattr(effective_auth, "username", None) or None
    merged_extras, hint_notes = apply_sbom_context_hints(
        sbom, chat_path, merged_extras, login_extras, auth_username=_auth_username
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

    # ── 7. Live probe ─────────────────────────────────────────────────────────
    # Option A — path unknown: full discovery (path + key).
    # Option B — path known but key is default: probe only the known path to
    #            detect the key; keep the user's path unchanged.
    _original_chat_path = chat_path
    chat_payload_value_template: "dict[str, object] | None" = None
    if not chat_path:
        # Option A: discover both path and key
        probe_result = await probe_chat_endpoints(
            target_url=target_url,
            sbom=sbom,
            auth_headers=effective_headers or None,
            known_payload_key=None,
            known_payload_list=chat_payload_list,
            probe_payload_extras=_probe_extras or None,
        )
        if probe_result is not None:
            chat_path, chat_payload_key, chat_payload_list = probe_result
            chat_payload_value_template = probe_result.value_template
            _log.info("resolve_target_session: live probe selected endpoint %s", chat_path)
    elif chat_payload_key == "message":
        # Option B: path is known but key is still the default — detect key only
        probe_result = await probe_chat_endpoints(
            target_url=target_url,
            sbom=sbom,
            auth_headers=effective_headers or None,
            known_payload_key=None,
            known_payload_list=chat_payload_list,
            probe_payload_extras=_probe_extras or None,
            hint_path=chat_path,
        )
        if probe_result is not None:
            _probe_path, chat_payload_key, chat_payload_list = probe_result
            chat_payload_value_template = probe_result.value_template
            # Keep the user's path — only the key and list are updated
            _log.info(
                "resolve_target_session: key detection on %s found key=%r list=%s template=%s",
                chat_path, chat_payload_key, chat_payload_list, bool(chat_payload_value_template),
            )

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
            chat_payload_value_template=chat_payload_value_template,
            ws_auth_message=ws_auth_message,
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
            chat_payload_value_template=chat_payload_value_template,
        ),
        health_report,
    )
