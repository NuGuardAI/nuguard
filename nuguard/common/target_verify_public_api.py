"""Public Pydantic APIs for target verification and session resolution."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.discovery import (
    DiscoveredProfile,
    DiscoveryRequest,
    TargetDiscoveryResult,
    cached_discovery_profile,
    profile_cache_fingerprint,
    run_discovery,
)
from nuguard.common.endpoint_detection.sbom import (
    discover_chat_candidates_from_sbom,
)
from nuguard.common.endpoint_preflight import (
    DEFAULT_PREFLIGHT_CANDIDATES,
    cached_endpoint_resolution,
    persist_endpoint_resolution,
    validate_and_rotate_chat_endpoint,
)
from nuguard.common.logging import get_logger
from nuguard.common.session_resolver import resolve_target_session
from nuguard.common.target_client_builder import build_target_app_client_from_session
from nuguard.models.health_report import CredentialCheckResult
from nuguard.redteam.target.session import AttackSession

if TYPE_CHECKING:
    from pathlib import Path

    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

TargetVerifyStatus = Literal[
    "ok",
    "auth_failed",
    "target_unavailable",
    "endpoint_not_found",
    "skipped",
]

EndpointSource = Literal["config", "sbom", "probe", "default", "enriched_sbom_cache"]


class TargetVerifyRequest(BaseModel):
    target_url: str
    chat_path: str | None = None
    auth_type: str = "none"
    auth_value: str | None = None
    auth_username: str | None = None
    auth_password: str | None = None
    login_flow: LoginFlowConfig | None = None
    headers: dict[str, str] | None = None
    chat_payload_key: str = "message"
    chat_payload_list: bool = False
    chat_response_key: str | None = None
    chat_payload_extras: dict[str, Any] | None = None
    request_timeout: float = 30.0
    discovery_max_turns: int = 3
    preflight_candidates: int = Field(
        default=DEFAULT_PREFLIGHT_CANDIDATES,
        description=(
            "How many alternative SBOM candidates to test when the resolved chat "
            "endpoint's reply isn't clearly conversational, before running pre-scan "
            "discovery — mirrors redteam.preflight_candidates / "
            "behavior.preflight_candidates so Target Verify validates the same "
            "endpoint candidate pool a subsequent Behavior/Redteam run would."
        ),
    )

    @model_validator(mode="after")
    def _validate_login_flow(self) -> "TargetVerifyRequest":
        if self.auth_type.strip().lower() == "login_flow" and self.login_flow is None:
            raise ValueError("auth_type=login_flow requires login_flow configuration")
        if self.auth_type.strip().lower() == "cookie_file" and not self.auth_value:
            raise ValueError(
                "auth_type=cookie_file requires auth_value to be the path to a "
                "Netscape-format cookies.txt"
            )
        return self


class TargetVerifyCheck(BaseModel):
    identity: str
    status: TargetVerifyStatus
    http_status_code: int | None = None
    response_time_ms: float | None = None
    endpoint: str
    error_detail: str | None = None
    # Set only for a 400/422 "ok" result — the endpoint is real but rejected
    # the probe's minimal payload; may need chat_payload_extras or a
    # different payload key. See CredentialCheckResult.payload_hint.
    payload_hint: str | None = None


class TargetVerifyResult(BaseModel):
    """``all_ok`` is False both when a credential check fails and when every
    credential checks out but no working chat endpoint could be validated
    (see ``checks`` for an entry with ``identity="endpoint"`` in that case —
    endpoint-preflight failure is always structurally visible there, not
    only in ``discovery_notes``)."""

    all_ok: bool
    endpoint: str
    discovered_endpoint: str | None = None
    endpoint_source: EndpointSource
    checks: list[TargetVerifyCheck] = Field(default_factory=list)
    discovered_profile: "DiscoveredProfile | None" = None
    discovery_hint: dict[str, Any] | None = None
    discovery_notes: list[str] = Field(default_factory=list)


class TargetSessionResolveRequest(BaseModel):
    target_url: str
    chat_path: str | None = None
    auth_type: str = "none"
    auth_value: str | None = None
    auth_username: str | None = None
    auth_password: str | None = None
    login_flow: LoginFlowConfig | None = None
    headers: dict[str, str] | None = None
    chat_payload_key: str = "message"
    chat_payload_list: bool = False
    chat_response_key: str | None = None
    chat_payload_extras: dict[str, Any] | None = None
    request_timeout: float = 30.0

    @model_validator(mode="after")
    def _validate_login_flow(self) -> "TargetSessionResolveRequest":
        if self.auth_type.strip().lower() == "login_flow" and self.login_flow is None:
            raise ValueError("auth_type=login_flow requires login_flow configuration")
        if self.auth_type.strip().lower() == "cookie_file" and not self.auth_value:
            raise ValueError(
                "auth_type=cookie_file requires auth_value to be the path to a "
                "Netscape-format cookies.txt"
            )
        return self


class TargetSessionResolveResult(BaseModel):
    effective_target_url: str
    effective_endpoint: str
    endpoint_source: EndpointSource
    chat_payload_key: str
    chat_payload_list: bool
    chat_response_key: str | None = None
    chat_payload_extras: dict[str, Any] = Field(default_factory=dict)
    discovery_notes: list[str] = Field(default_factory=list)
    health_report: dict[str, Any] | None = None


def _build_auth_config(request: TargetVerifyRequest | TargetSessionResolveRequest) -> AuthConfig:
    auth_type = (request.auth_type or "none").strip().lower()
    if auth_type == "none":
        return AuthConfig(type="none")
    if auth_type == "basic":
        return AuthConfig(
            type="basic",
            username=request.auth_username or "",
            password=request.auth_password or "",
        )
    if auth_type == "login_flow":
        if request.login_flow is None:
            raise ValueError("auth_type=login_flow requires login_flow configuration")
        return AuthConfig(type="login_flow", login_flow=request.login_flow)
    if auth_type == "bearer":
        value = request.auth_value or ""
        if value.lower().startswith("authorization:"):
            header = value
        elif value.lower().startswith("bearer "):
            header = f"Authorization: {value}"
        else:
            header = f"Authorization: Bearer {value}"
        return AuthConfig(type="bearer", header=header)
    if auth_type == "api_key":
        value = request.auth_value or ""
        header = value if ":" in value else f"X-API-Key: {value}"
        return AuthConfig(type="api_key", header=header)
    if auth_type == "cookie_file":
        return AuthConfig(type="cookie_file", cookie_file=request.auth_value or "")
    return AuthConfig(type="none")


def _merge_headers(
    request: "TargetVerifyRequest | TargetSessionResolveRequest",
    *header_dicts: dict[str, str] | None,
) -> dict[str, str]:
    """Merge request.headers underneath the given auth/bootstrap headers.

    Custom headers apply to every request; auth-derived headers win on any
    key conflict, mirroring how BehaviorConfig.headers is merged in
    ``BehaviorRunner._build_client``.
    """
    merged: dict[str, str] = dict(request.headers or {})
    for headers in header_dicts:
        if headers:
            merged.update(headers)
    return merged


def _check_from_health(check: CredentialCheckResult) -> TargetVerifyCheck:
    status = check.status
    if status not in {"ok", "auth_failed", "target_unavailable", "endpoint_not_found", "skipped"}:
        status = "target_unavailable"
    return TargetVerifyCheck(
        identity=check.identity,
        status=status,
        http_status_code=check.http_status_code,
        response_time_ms=check.response_time_ms,
        endpoint=check.endpoint,
        error_detail=check.error_detail or None,
        payload_hint=check.payload_hint or None,
    )


async def verify_target(
    request: TargetVerifyRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
    config_path: "Path | None" = None,
    sbom_path: "Path | None" = None,
) -> TargetVerifyResult:
    auth_config = _build_auth_config(request)
    endpoint_explicit = "chat_path" in request.model_fields_set
    session_cfg, health = await resolve_target_session(
        target_url=request.target_url,
        sbom=sbom,
        auth_config=auth_config,
        extra_headers=dict(request.headers or {}),
        chat_path=request.chat_path or "",
        chat_payload_key=request.chat_payload_key,
        chat_payload_list=request.chat_payload_list,
        chat_payload_extras=dict(request.chat_payload_extras or {}),
        chat_response_key=request.chat_response_key,
        probe_payload_extras=request.chat_payload_extras or None,
        config_path=config_path,
        request_timeout=request.request_timeout,
        endpoint_explicit=endpoint_explicit,
        payload_key_explicit="chat_payload_key" in request.model_fields_set,
        response_key_explicit="chat_response_key" in request.model_fields_set,
    )

    checks = [_check_from_health(item) for item in health.checks]
    all_ok = all(item.status in ("ok", "skipped") for item in checks)
    discovered_profile = None
    discovery_notes: list[str] = []
    endpoint = session_cfg.chat_path
    endpoint_source = session_cfg.endpoint_source

    if all_ok:
        client = build_target_app_client_from_session(
            session_cfg,
            timeout=request.request_timeout,
        )
        async with client:
            # Validate (and if needed rotate) the chat endpoint, and bootstrap
            # any templated path param (e.g. :id on a create-conversation-then-
            # post-message route) — the same preflight RedteamOrchestrator.run()
            # and BehaviorRunner already run before their own pre-scan discovery.
            # Without this, every discovery turn against a templated endpoint
            # fails with "[CONFIG_ERROR: unresolved path param ...]" and Target
            # Verify silently reports no profile — see issue #611.
            #
            # First check for a previously-validated resolution cached on the
            # SBOM by an earlier Target Verify/Behavior/Redteam run against
            # this same target/auth — reusing it skips the live round-trip
            # entirely. A cache entry for a DIFFERENT path than the one
            # explicitly configured here is never used (config-wins
            # precedence; see endpoint-resolution-precedence-plan.md).
            _sbom_hit = cached_endpoint_resolution(
                sbom,
                session_cfg.base_url,
                auth_config,
                required_chat_path=endpoint if endpoint_explicit else None,
            )
            preflight_ok: bool
            preflight_notes: list[str]
            if _sbom_hit is not None:
                _resolved, _resolved_params = _sbom_hit
                client.set_chat_endpoint(
                    _resolved.chat_path, _resolved.chat_payload_key,
                    _resolved.chat_payload_list, _resolved.chat_response_key,
                )
                for _pp_name, _pp_value in _resolved_params.items():
                    client.set_path_param(_pp_name, _pp_value)
                endpoint = _resolved.chat_path
                if (
                    _resolved.endpoint_source
                    and not endpoint_explicit
                    and _resolved.endpoint_source != "config"
                ):
                    # "config" is only a truthful source label for the run
                    # that actually configured the endpoint explicitly — a
                    # non-explicit run reusing that run's cached resolution
                    # must not inherit the label.
                    endpoint_source = cast(EndpointSource, _resolved.endpoint_source)
                preflight_ok = True
                preflight_notes = [
                    f"Chat endpoint {endpoint!r} reused from a previously-validated "
                    "SBOM resolution — skipped live preflight."
                ]
                discovery_notes.extend(preflight_notes)
            else:
                preflight = await validate_and_rotate_chat_endpoint(
                    client,
                    sbom,
                    has_explicit_endpoint=endpoint_explicit,
                    target_url=session_cfg.base_url,
                    auth_headers=session_cfg.effective_headers or None,
                    max_candidates=request.preflight_candidates,
                )
                discovery_notes.extend(preflight.notes)
                # Prefer the rotation outcome itself over re-reading
                # client.*: self-contained rather than relying on
                # validate_and_rotate_chat_endpoint having mutated *client*
                # as a side effect.
                if preflight.rotated_endpoint is not None:
                    endpoint, _rot_payload_key, _rot_payload_list, _rot_response_key = (
                        preflight.rotated_endpoint
                    )
                    if preflight.endpoint_source is not None:
                        endpoint_source = preflight.endpoint_source
                else:
                    _rot_payload_key = getattr(client, "_chat_payload_key", "message")
                    _rot_payload_list = bool(getattr(client, "_chat_payload_list", False))
                    _rot_response_key = getattr(client, "_chat_response_key", None)
                preflight_ok = preflight.ok
                preflight_notes = preflight.notes
                if preflight_ok and sbom is not None:
                    persist_endpoint_resolution(
                        sbom,
                        session_cfg.base_url,
                        auth_config,
                        chat_path=endpoint,
                        chat_payload_key=_rot_payload_key,
                        chat_payload_list=_rot_payload_list,
                        chat_response_key=_rot_response_key,
                        endpoint_source=endpoint_source,
                        path_param_values=dict(getattr(client, "path_param_values", None) or {}),
                    )
                    if sbom_path is not None:
                        from nuguard.common.auto_sbom_enricher import (  # noqa: PLC0415
                            persist_endpoint_resolution_sbom,
                        )

                        try:
                            _ep_artifact = persist_endpoint_resolution_sbom(sbom, sbom_path)
                        except Exception as exc:
                            _log.warning(
                                "verify_target: could not persist endpoint resolution: %s", exc
                            )
                        else:
                            _log.info(
                                "verify_target: persisted endpoint resolution to %s", _ep_artifact
                            )

            if not preflight_ok:
                all_ok = False
                # Structured signal, not just a free-text note: a caller
                # inspecting `checks` to find out why all_ok is False must
                # see something there, not just credential checks that all
                # say "ok" with the real reason buried in discovery_notes.
                checks.append(
                    TargetVerifyCheck(
                        identity="endpoint",
                        status="endpoint_not_found",
                        endpoint=endpoint,
                        error_detail=(
                            "; ".join(preflight_notes)
                            or "No working chat endpoint found during preflight validation."
                        ),
                    )
                )
            else:
                # Same cache-then-fall-back pattern as the endpoint
                # resolution above, applied to the discovered identity
                # profile (issue #611 Phase 2) — previously wired into
                # BehaviorRunner/RedteamOrchestrator but never into this
                # function, so every verify_target() call re-ran the live
                # DISCOVER conversation regardless of a prior successful
                # discovery against the same target/auth.
                _cached_profile = cached_discovery_profile(sbom, session_cfg.base_url, auth_config)
                if _cached_profile is not None:
                    discovered_profile = _cached_profile
                    discovery_notes.append(
                        f"Pre-scan discovery (from enriched SBOM): "
                        f"name={_cached_profile.customer_name!r} ids={_cached_profile.ids}"
                    )
                else:
                    outcome = await run_discovery(
                        client,
                        AttackSession(
                            session_id=f"verify-{uuid.uuid4()}",
                            target_url=session_cfg.base_url,
                            chain_id="verify-target",
                        ),
                        DiscoveryRequest(
                            use_case=getattr(getattr(sbom, "summary", None), "use_case", "") if sbom is not None else "",
                            max_turns=request.discovery_max_turns,
                            fallback_endpoints=(
                                discover_chat_candidates_from_sbom(sbom)
                                if sbom is not None
                                else []
                            ),
                        ),
                    )
                    discovered_profile = outcome.profile if not outcome.profile.is_empty else None
                    discovery_notes.extend(outcome.notes)
                    if discovered_profile is not None and sbom is not None:
                        sbom.discovered_profile = discovered_profile.model_dump(mode="json")
                        sbom.discovered_profile_fingerprint = profile_cache_fingerprint(
                            session_cfg.base_url, auth_config
                        )
                        if sbom_path is not None:
                            from nuguard.common.auto_sbom_enricher import (  # noqa: PLC0415
                                persist_discovery_profile_sbom,
                            )

                            try:
                                persist_discovery_profile_sbom(sbom, sbom_path)
                            except Exception as exc:
                                _log.warning(
                                    "verify_target: could not persist discovery profile: %s", exc
                                )

    return TargetVerifyResult(
        all_ok=all_ok,
        endpoint=endpoint,
        discovered_endpoint=(
            endpoint if endpoint_source in ("sbom", "probe") else None
        ),
        endpoint_source=cast(
            EndpointSource,
            endpoint_source if endpoint_source in {"config", "sbom", "probe", "default"} else "default",
        ),
        checks=checks,
        discovered_profile=discovered_profile,
        discovery_hint={"endpoint_source": endpoint_source},
        discovery_notes=discovery_notes,
    )


async def resolve_target_session_public(
    request: TargetSessionResolveRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
    config_path: "Path | None" = None,
) -> TargetSessionResolveResult:
    auth_config = _build_auth_config(request)
    session_cfg, health = await resolve_target_session(
        target_url=request.target_url,
        sbom=sbom,
        auth_config=auth_config,
        extra_headers=dict(request.headers or {}),
        chat_path=request.chat_path or "",
        chat_payload_key=request.chat_payload_key,
        chat_payload_list=request.chat_payload_list,
        chat_payload_extras=dict(request.chat_payload_extras or {}),
        chat_response_key=request.chat_response_key,
        probe_payload_extras=request.chat_payload_extras or None,
        config_path=config_path,
        request_timeout=request.request_timeout,
        endpoint_explicit="chat_path" in request.model_fields_set,
        payload_key_explicit="chat_payload_key" in request.model_fields_set,
        response_key_explicit="chat_response_key" in request.model_fields_set,
    )
    return TargetSessionResolveResult(
        effective_target_url=session_cfg.base_url,
        effective_endpoint=session_cfg.chat_path,
        endpoint_source=cast(
            EndpointSource,
            session_cfg.endpoint_source
            if session_cfg.endpoint_source in {"config", "sbom", "probe", "default"}
            else "default",
        ),
        chat_payload_key=session_cfg.chat_payload_key,
        chat_payload_list=session_cfg.chat_payload_list,
        chat_response_key=session_cfg.chat_response_key,
        chat_payload_extras=dict(session_cfg.chat_payload_extras),
        discovery_notes=list(session_cfg.resolution_notes),
        health_report=health.model_dump(mode="json"),
    )


__all__ = [
    "TargetVerifyStatus",
    "EndpointSource",
    "TargetVerifyRequest",
    "TargetVerifyCheck",
    "TargetVerifyResult",
    "TargetSessionResolveRequest",
    "TargetSessionResolveResult",
    "verify_target",
    "resolve_target_session_public",
    "TargetDiscoveryResult",
]
