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
    run_discovery,
)
from nuguard.common.endpoint_detection.sbom import (
    discover_chat_candidates_from_sbom,
)
from nuguard.common.session_resolver import resolve_target_session
from nuguard.common.target_client_builder import build_target_app_client_from_session
from nuguard.models.health_report import CredentialCheckResult
from nuguard.redteam.target.session import AttackSession

if TYPE_CHECKING:
    from pathlib import Path

    from nuguard.sbom.models import AiSbomDocument

TargetVerifyStatus = Literal[
    "ok",
    "auth_failed",
    "target_unavailable",
    "endpoint_not_found",
    "skipped",
]

EndpointSource = Literal["config", "sbom", "probe", "default"]


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
) -> TargetVerifyResult:
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

    checks = [_check_from_health(item) for item in health.checks]
    all_ok = all(item.status in ("ok", "skipped") for item in checks)
    discovered_profile = None
    discovery_notes: list[str] = []

    if all_ok:
        client = build_target_app_client_from_session(
            session_cfg,
            timeout=request.request_timeout,
        )
        async with client:
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
        discovery_notes = outcome.notes

    return TargetVerifyResult(
        all_ok=all_ok,
        endpoint=session_cfg.chat_path,
        discovered_endpoint=(
            session_cfg.chat_path
            if session_cfg.endpoint_source in ("sbom", "probe")
            else None
        ),
        endpoint_source=cast(
            EndpointSource,
            session_cfg.endpoint_source
            if session_cfg.endpoint_source in {"config", "sbom", "probe", "default"}
            else "default",
        ),
        checks=checks,
        discovered_profile=discovered_profile,
        discovery_hint={"endpoint_source": session_cfg.endpoint_source},
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
