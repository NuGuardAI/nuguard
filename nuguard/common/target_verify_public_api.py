"""Public Pydantic APIs for target verification and session resolution."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.auth_runtime import bootstrap_auth_runtime
from nuguard.common.discovery import (
    DiscoveredProfile,
    DiscoveryRequest,
    TargetDiscoveryResult,
    run_discovery,
)
from nuguard.common.endpoint_probe import (
    discover_chat_candidates_from_sbom,
    discover_chat_config_from_sbom,
    probe_chat_endpoints,
)
from nuguard.common.session_resolver import resolve_target_session
from nuguard.common.target_client_builder import build_target_app_client, resolve_target_url
from nuguard.models.health_report import CredentialCheckResult
from nuguard.redteam.target.session import AttackSession

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

TargetVerifyStatus = Literal[
    "ok",
    "auth_failed",
    "target_unavailable",
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
    if status not in {"ok", "auth_failed", "target_unavailable", "skipped"}:
        status = "target_unavailable"
    return TargetVerifyCheck(
        identity=check.identity,
        status=status,
        http_status_code=check.http_status_code,
        response_time_ms=check.response_time_ms,
        endpoint=check.endpoint,
        error_detail=check.error_detail or None,
    )


async def _resolve_endpoint_plan(
    *,
    target_url: str,
    sbom: "AiSbomDocument | None",
    auth_headers: dict[str, str] | None,
    chat_path: str | None,
    chat_payload_key: str,
    chat_payload_list: bool,
    chat_response_key: str | None,
    chat_payload_extras: dict[str, Any] | None,
) -> tuple[str, str, bool, str | None, EndpointSource]:
    if chat_path:
        return chat_path, chat_payload_key, chat_payload_list, chat_response_key, "config"

    if sbom is not None:
        path, key, payload_list, response_key = discover_chat_config_from_sbom(
            sbom,
            chat_path="",
            chat_payload_key=chat_payload_key,
            chat_payload_list=chat_payload_list,
        )
        if path:
            source: EndpointSource = "sbom"
            if response_key and not chat_response_key:
                chat_response_key = response_key
            return path, key, payload_list, chat_response_key, source

        probed = await probe_chat_endpoints(
            target_url=target_url,
            sbom=sbom,
            auth_headers=auth_headers,
            known_payload_key=chat_payload_key if chat_payload_key != "message" else None,
            known_payload_list=chat_payload_list,
            known_response_key=chat_response_key,
            probe_payload_extras=chat_payload_extras,
        )
        if probed is not None:
            p_path, p_key, p_list = probed
            return p_path, p_key, p_list, chat_response_key, "probe"

    return "/chat", chat_payload_key, chat_payload_list, chat_response_key, "default"


async def verify_target(
    request: TargetVerifyRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
) -> TargetVerifyResult:
    auth_config = _build_auth_config(request)

    resolved_url, _ = resolve_target_url(request.target_url, sbom)
    endpoint, payload_key, payload_list, response_key, endpoint_source = await _resolve_endpoint_plan(
        target_url=resolved_url,
        sbom=sbom,
        auth_headers=_merge_headers(request, auth_config.to_headers()) or None,
        chat_path=request.chat_path,
        chat_payload_key=request.chat_payload_key,
        chat_payload_list=request.chat_payload_list,
        chat_response_key=request.chat_response_key,
        chat_payload_extras=request.chat_payload_extras,
    )

    bootstrapper, health = await bootstrap_auth_runtime(
        target_url=resolved_url,
        endpoint=endpoint,
        auth_config=auth_config,
        run_id=str(uuid.uuid4()),
        timeout=request.request_timeout,
        probe_payload_extras=request.chat_payload_extras or None,
    )

    checks = [_check_from_health(item) for item in health.checks]
    all_ok = all(item.status in ("ok", "skipped") for item in checks)
    discovered_profile = None
    discovery_notes: list[str] = []

    if all_ok:
        client = build_target_app_client(
            resolved_url,
            endpoint=endpoint,
            payload_key=payload_key,
            payload_list=payload_list,
            response_key=response_key,
            timeout=request.request_timeout,
            auth_headers=_merge_headers(
                request, auth_config.to_headers(), bootstrapper.session.headers()
            )
            or None,
            sbom=sbom,
            payload_extras=request.chat_payload_extras or None,
        )
        async with client:
            outcome = await run_discovery(
                client,
                AttackSession(
                    session_id=f"verify-{uuid.uuid4()}",
                    target_url=resolved_url,
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
        endpoint=endpoint,
        discovered_endpoint=endpoint if endpoint_source in ("sbom", "probe") else None,
        endpoint_source=endpoint_source,
        checks=checks,
        discovered_profile=discovered_profile,
        discovery_hint={"endpoint_source": endpoint_source},
        discovery_notes=discovery_notes,
    )


async def resolve_target_session_public(
    request: TargetSessionResolveRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
) -> TargetSessionResolveResult:
    auth_config = _build_auth_config(request)

    if sbom is not None:
        resolved_url, _ = resolve_target_url(request.target_url, sbom)
        endpoint, payload_key, payload_list, response_key, endpoint_source = await _resolve_endpoint_plan(
            target_url=resolved_url,
            sbom=sbom,
            auth_headers=_merge_headers(request, auth_config.to_headers()) or None,
            chat_path=request.chat_path,
            chat_payload_key=request.chat_payload_key,
            chat_payload_list=request.chat_payload_list,
            chat_response_key=request.chat_response_key,
            chat_payload_extras=request.chat_payload_extras,
        )
        session_cfg, health = await resolve_target_session(
            target_url=resolved_url,
            sbom=sbom,
            auth_config=auth_config,
            extra_headers=dict(request.headers or {}),
            chat_path=endpoint,
            chat_payload_key=payload_key,
            chat_payload_list=payload_list,
            chat_payload_extras=request.chat_payload_extras or {},
            chat_response_key=response_key,
        )
        return TargetSessionResolveResult(
            effective_target_url=session_cfg.base_url,
            effective_endpoint=session_cfg.chat_path,
            endpoint_source=endpoint_source,
            chat_payload_key=session_cfg.chat_payload_key,
            chat_payload_list=session_cfg.chat_payload_list,
            chat_response_key=session_cfg.chat_response_key,
            chat_payload_extras=dict(session_cfg.chat_payload_extras),
            discovery_notes=list(session_cfg.resolution_notes),
            health_report=health.model_dump(mode="json"),
        )

    resolved_url, _ = resolve_target_url(request.target_url, None)
    endpoint = request.chat_path or "/chat"
    bootstrapper, health = await bootstrap_auth_runtime(
        target_url=resolved_url,
        endpoint=endpoint,
        auth_config=auth_config,
        run_id=str(uuid.uuid4()),
        timeout=request.request_timeout,
        probe_payload_extras=request.chat_payload_extras or None,
    )
    _ = bootstrapper
    return TargetSessionResolveResult(
        effective_target_url=resolved_url,
        effective_endpoint=endpoint,
        endpoint_source="config" if request.chat_path else "default",
        chat_payload_key=request.chat_payload_key,
        chat_payload_list=request.chat_payload_list,
        chat_response_key=request.chat_response_key,
        chat_payload_extras=dict(request.chat_payload_extras or {}),
        discovery_notes=[],
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
