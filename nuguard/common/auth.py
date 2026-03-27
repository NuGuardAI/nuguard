"""Auth configuration and header resolution for NuGuard target connections.

Supports four auth types:
    bearer   — Authorization: Bearer <token>
    api_key  — Custom header: <header-name>: <value>
    basic    — Authorization: Basic <base64(username:password)>
    none     — No Authorization header

The structured form (type + fields) is preferred. The legacy flat
auth_header string ("Header-Name: value") is still accepted and
parsed into an equivalent AuthConfig.
"""
from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, model_validator


class AuthConfig(BaseModel):
    """Structured auth configuration parsed from nuguard.yaml auth block."""

    type: Literal["bearer", "api_key", "basic", "none"] = "none"

    # bearer / api_key: full header string e.g. "Authorization: Bearer tok"
    header: str = ""

    # basic: plaintext username and password (resolved from env vars before this model is built)
    username: str = ""
    password: str = ""

    @model_validator(mode="after")
    def _validate_fields(self) -> "AuthConfig":
        if self.type == "bearer" and not self.header:
            raise ValueError("auth.type=bearer requires auth.header")
        if self.type == "api_key" and not self.header:
            raise ValueError("auth.type=api_key requires auth.header")
        if self.type == "basic" and not (self.username and self.password):
            raise ValueError("auth.type=basic requires auth.username and auth.password")
        return self

    def to_headers(self) -> dict[str, str]:
        """Return the HTTP headers dict to be merged into every request."""
        if self.type == "none":
            return {}
        if self.type in ("bearer", "api_key"):
            # header field is "Header-Name: value"
            name, _, value = self.header.partition(":")
            return {name.strip(): value.strip()}
        if self.type == "basic":
            credential = base64.b64encode(
                f"{self.username}:{self.password}".encode()
            ).decode()
            return {"Authorization": f"Basic {credential}"}
        return {}

    @classmethod
    def from_header_string(cls, auth_header: str) -> "AuthConfig":
        """Parse the legacy flat auth_header string into an AuthConfig.

        Accepts "Authorization: Bearer tok" or "X-API-Key: key".
        Treats any Authorization: Bearer... value as type=bearer;
        everything else as type=api_key.
        """
        if not auth_header or not auth_header.strip():
            return cls(type="none")
        name, _, value = auth_header.partition(":")
        name = name.strip()
        value = value.strip()
        header_string = f"{name}: {value}"
        if name.lower() == "authorization" and value.lower().startswith("bearer"):
            return cls(type="bearer", header=header_string)
        return cls(type="api_key", header=header_string)

    @classmethod
    def from_tenant_token(cls, session_token: str) -> "AuthConfig":
        """Build an AuthConfig from a canary.json tenant session_token.

        A token that starts with a header name and colon (e.g. "X-API-Key: tok")
        is treated as api_key. Anything else is treated as a bare bearer token.
        """
        if not session_token:
            return cls(type="none")
        if ":" in session_token.split()[0]:
            return cls.from_header_string(session_token)
        return cls(type="bearer", header=f"Authorization: Bearer {session_token}")
