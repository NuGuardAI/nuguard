"""Public Pydantic API for the browser-login discovery fallback.

A thin, JSON-safe wrapper around :class:`BrowserLoginSession` for callers
outside the CLI: a Pydantic request in, a Pydantic result out — matching the
``public_api.py`` convention used by every other capability (see e.g.
``nuguard/common/target_verify_public_api.py``, ``nuguard/behavior/public_api.py``).

Playwright is only imported when :func:`discover_browser` actually runs (via
``BrowserLoginSession``, which imports it lazily) — importing this module
never requires the ``browser`` extra.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from nuguard.common.auth import AuthConfig
from nuguard.common.browser_login.config import BrowserDiscoveryConfig
from nuguard.common.browser_login.session import BrowserLoginSession


class BrowserDiscoveryRequest(BaseModel):
    """JSON-safe input for :func:`discover_browser`."""

    target_url: str
    auth_type: str = "none"
    auth_value: str | None = None
    auth_username: str | None = None
    auth_password: str | None = None
    headless: bool = True
    timeout_s: int = 30
    sniff_chat: bool = True
    chat_message: str = "Hello"
    browser_discovery: BrowserDiscoveryConfig = Field(default_factory=BrowserDiscoveryConfig)


class BrowserDiscoveryResult(BaseModel):
    """JSON-safe result of a :func:`discover_browser` run."""

    cookies_written_to: str
    identity_url: str | None = None
    identity_payload: dict[str, Any] | None = None
    sniffed_endpoint: str | None = None
    sniffed_chat_request: dict[str, Any] | None = None
    candidate_extra_fields: dict[str, str] = Field(default_factory=dict)
    ambiguous_fields: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def _build_auth_config(request: BrowserDiscoveryRequest) -> AuthConfig:
    auth_type = (request.auth_type or "none").strip().lower()
    if auth_type == "basic":
        return AuthConfig(
            type="basic",
            username=request.auth_username or "",
            password=request.auth_password or "",
        )
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
    return AuthConfig(type="none")


async def discover_browser(
    request: BrowserDiscoveryRequest,
    *,
    cookie_file: Path,
) -> BrowserDiscoveryResult:
    """Log in via a real browser and capture the resulting session.

    ``cookie_file`` is a filesystem destination for the exported session
    cookies, not JSON-safe run configuration, so it stays a keyword-only
    argument rather than a request field (mirrors how ``sbom`` is kept out of
    ``TargetVerifyRequest`` in ``target_verify_public_api.py``).

    Raises :class:`BrowserLoginError` on any unrecoverable step failure —
    this wrapper does not catch or suppress exceptions.
    """
    auth_config = _build_auth_config(request)

    async with BrowserLoginSession(
        request.target_url,
        auth_config,
        request.browser_discovery,
        headless=request.headless,
        timeout_s=request.timeout_s,
    ) as session:
        result = await session.run(chat_message=request.chat_message, sniff_chat=request.sniff_chat)
        await session.export_cookies(cookie_file)

    return BrowserDiscoveryResult(
        cookies_written_to=str(cookie_file),
        identity_url=result.identity_url,
        identity_payload=result.identity_payload,
        sniffed_endpoint=result.sniffed_endpoint,
        sniffed_chat_request=result.sniffed_chat_request,
        candidate_extra_fields=result.candidate_extra_fields,
        ambiguous_fields=result.ambiguous_fields,
        warnings=result.warnings,
    )


__all__ = [
    "BrowserDiscoveryRequest",
    "BrowserDiscoveryResult",
    "discover_browser",
]
