"""Identity a conversation branch is pinned to.

A :class:`Principal` carries a *reference* (a stable label such as
``"primary"`` or ``"canary:tenant-b"``) and an auth-scope fingerprint — a hash
of the credential material, never the credential itself — so caches and
branches can be keyed by identity without secrets reaching logs or reports.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_AUTH_SCOPE_SALT = b"nuguard:campaign:auth-scope:v1"
_AUTH_SCOPE_ITERATIONS = 600_000


def _fingerprint(headers: dict[str, str]) -> str:
    """Derive a stable identity label without cheaply guessable password hashes.

    The public, purpose-specific salt is fixed so separate processes can resume
    checkpoints. This label is not a password verifier; password storage still
    requires a random per-password salt. Keep the full 256-bit derived value.
    """
    if not headers:
        return "anonymous"
    canon = json.dumps(sorted((k.lower(), v) for k, v in headers.items()), separators=(",", ":"))
    return hashlib.pbkdf2_hmac(
        "sha256", canon.encode("utf-8"), _AUTH_SCOPE_SALT, _AUTH_SCOPE_ITERATIONS, dklen=32
    ).hex()


@dataclass(frozen=True)
class Principal:
    """An authenticated (or anonymous) caller identity."""

    ref: str
    auth_scope: str
    headers: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def anonymous(cls) -> "Principal":
        return cls(ref="anonymous", auth_scope="anonymous")

    @classmethod
    def from_headers(cls, ref: str, headers: dict[str, str]) -> "Principal":
        """Build from the auth headers this identity sends (e.g. ``AuthSession.headers()``)."""
        return cls(ref=ref, auth_scope=_fingerprint(headers), headers=dict(headers))

    @classmethod
    def from_canary_tenant(cls, tenant: Any) -> "Principal":
        """Build from a ``CanaryTenant`` that has a ``session_token``."""
        from nuguard.common.auth import AuthConfig

        headers = AuthConfig.from_tenant_token(tenant.session_token).to_headers()
        return cls.from_headers(f"canary:{tenant.tenant_id}", headers)
