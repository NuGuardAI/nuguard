"""Shared auth runtime helpers used by behavior and redteam flows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from nuguard.common.auth import AuthConfig
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.models.health_report import TargetHealthReport
from nuguard.redteam.target.canary import CanaryConfig


@dataclass(frozen=True)
class ResolvedAuthRuntime:
    """Resolved auth config and initial headers for a runtime flow."""

    auth_config: AuthConfig
    initial_headers: dict[str, str]


def _normalize_headers(headers_override: Mapping[str, str] | None) -> dict[str, str]:
    if not headers_override:
        return {}
    return {
        str(name): str(value)
        for name, value in headers_override.items()
        if str(name).strip()
    }


def _pick_auth_seed_header(headers: Mapping[str, str]) -> tuple[str, str]:
    """Pick the most likely auth header to derive an AuthConfig from."""
    for name, value in headers.items():
        if name.lower() == "authorization":
            return name, value
    return next(iter(headers.items()))


def resolve_auth_runtime(
    auth_config: AuthConfig | None = None,
    headers_override: Mapping[str, str] | None = None,
) -> ResolvedAuthRuntime:
    """Resolve effective auth with explicit-header override precedence.

    Precedence:
    1) headers_override map (if provided)
    2) structured AuthConfig
    3) AuthConfig(type="none")
    """
    resolved_override = _normalize_headers(headers_override)
    if resolved_override:
        name, value = _pick_auth_seed_header(resolved_override)
        derived_auth = AuthConfig.from_header_string(f"{name}: {value}")
        return ResolvedAuthRuntime(
            auth_config=derived_auth,
            initial_headers=resolved_override,
        )

    effective_auth = auth_config or AuthConfig(type="none")
    return ResolvedAuthRuntime(
        auth_config=effective_auth,
        initial_headers=effective_auth.to_headers(),
    )


async def bootstrap_auth_runtime(
    *,
    target_url: str,
    endpoint: str,
    auth_config: AuthConfig | None,
    canary_config: CanaryConfig | None = None,
    run_id: str | None = None,
    timeout: float | None = None,
) -> tuple[AuthBootstrapper, TargetHealthReport]:
    """Run shared auth bootstrap and return both bootstrapper and report."""
    bootstrapper = AuthBootstrapper(
        target_url=target_url,
        endpoint=endpoint,
        default_auth=auth_config,
        canary_config=canary_config,
        run_id=run_id,
        timeout=timeout,
    )
    report = await bootstrapper.run()
    return bootstrapper, report