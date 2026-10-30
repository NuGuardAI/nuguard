"""Canary data configuration and response scanner."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from nuguard.common.logging import get_logger

_log = get_logger(__name__)


class CanaryRecord(BaseModel):
    resource: str
    id: str
    fields: dict[str, Any] = Field(default_factory=dict)
    watch_values: list[str] = Field(default_factory=list)


class CanaryTenant(BaseModel):
    """One tenant / identity.

    A tenant authenticates with a static ``session_token`` (bearer token or a
    ``"Header: value"`` string), or — for cookie/CSRF apps with anonymous
    sessions — ``web_session: true``, which acquires an independent cookie +
    CSRF session at run start (see :func:`acquire_web_session_tenants`).
    """

    tenant_id: str
    session_token: str = ""
    # Acquire a fresh cookie/CSRF web session for this tenant at run start. Every
    # acquisition starts a new server-side session, so the tenant gets its own
    # namespace — a genuine second principal for cross-tenant scenarios.
    web_session: bool = False
    # UI pages to crawl for the session (default: "/"); e.g. ["/vendor/"].
    web_session_pages: list[str] = Field(default_factory=list)
    # In-memory auth headers (cookie/CSRF) — never serialised, so credentials
    # cannot leak into reports, checkpoints or schema dumps.
    session_headers: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)
    records: list[CanaryRecord] = Field(default_factory=list)

    @property
    def has_credentials(self) -> bool:
        """True when this tenant can act as a distinct principal."""
        return bool(self.session_token or self.session_headers)

    def auth_headers(self) -> dict[str, str]:
        """Request headers for this tenant (acquired web session wins over a static token)."""
        if self.session_headers:
            return dict(self.session_headers)
        from nuguard.common.auth import AuthConfig  # noqa: PLC0415

        return AuthConfig.from_tenant_token(self.session_token).to_headers()


class CanaryConfig(BaseModel):
    global_watch_values: list[str] = Field(default_factory=list)
    tenants: list[CanaryTenant] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "CanaryConfig":
        """Load a CanaryConfig from a JSON file."""
        data = json.loads(path.read_text())
        return cls.model_validate(data)

    def all_watch_values(self) -> list[str]:
        """Return a deduplicated list of all canary watch values."""
        values = list(self.global_watch_values)
        for tenant in self.tenants:
            for record in tenant.records:
                values.extend(record.watch_values)
        return list(set(values))


class CanaryScanner:
    """Scans a response string for canary values (verbatim or encoded)."""

    def __init__(self, config: CanaryConfig) -> None:
        self._watch = config.all_watch_values()

    def scan(self, text: str) -> list[str]:
        """Return list of matched canary values found verbatim in text."""
        return [v for v in self._watch if v and v in text]

    def scan_decoded(self, text: str) -> list[str]:
        """Return canary values found after decoding base64, hex, and unicode escapes."""
        found: list[str] = []
        # Try base64 decoding of text chunks
        try:
            decoded_b64 = base64.b64decode(text + "==", validate=False).decode(
                "utf-8", errors="ignore"
            )
            found.extend(v for v in self._watch if v and v in decoded_b64)
        except Exception:
            pass
        # Hex decode
        try:
            hex_clean = re.sub(r"[^0-9a-fA-F]", "", text)
            if len(hex_clean) >= 4 and len(hex_clean) % 2 == 0:
                decoded_hex = bytes.fromhex(hex_clean).decode("utf-8", errors="ignore")
                found.extend(v for v in self._watch if v and v in decoded_hex)
        except Exception:
            pass
        # Unicode escapes
        try:
            decoded_uni = text.encode().decode("unicode_escape", errors="ignore")
            found.extend(v for v in self._watch if v and v in decoded_uni)
        except Exception:
            pass
        return list(set(found))

    def has_match(self, text: str) -> bool:
        """Return True if any canary value is found verbatim or encoded."""
        return bool(self.scan(text) or self.scan_decoded(text))


async def acquire_web_session_tenants(
    config: CanaryConfig,
    base_url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> list[str]:
    """Acquire a cookie/CSRF session for each ``web_session`` tenant lacking credentials.

    Returns the ``tenant_id`` of every tenant that could not obtain a session
    (logged, never raised — a missing second principal only disables the
    scenarios that need it).
    """
    from nuguard.common.web_session import (  # noqa: PLC0415
        WebSessionBootstrapper,
        candidate_pages,
    )

    failed: list[str] = []
    for tenant in config.tenants:
        if not tenant.web_session or tenant.has_credentials:
            continue
        bootstrapper = WebSessionBootstrapper(base_url, timeout=timeout, headers=headers)
        session = await bootstrapper.acquire(candidate_pages(None, tenant.web_session_pages))
        session_headers = session.headers() if session is not None else {}
        if not session_headers:
            _log.warning("Canary tenant %r: could not acquire a web session", tenant.tenant_id)
            failed.append(tenant.tenant_id)
            continue
        tenant.session_headers = session_headers
        _log.info("Canary tenant %r: acquired an independent web session", tenant.tenant_id)
    return failed
