"""Auth configuration and session management for NuGuard target connections.

Supports four static auth types:
    bearer      — Authorization: Bearer <token>
    api_key     — Custom header: <header-name>: <value>
    basic       — Authorization: Basic <base64(username:password)>
    none        — No Authorization header

And a login-flow auth type for apps that issue short-lived JWTs:
    login_flow  — POST /login with credentials, extract token, attach as Bearer.
                  AuthSession handles token acquisition and 401-triggered refresh.

Both behavior and redteam modules use AuthSession — it is initialised once
by AuthBootstrapper before any scenarios run.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, model_validator

_log = logging.getLogger(__name__)

_LOGIN_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# LoginFlowConfig
# ---------------------------------------------------------------------------

class LoginFlowConfig(BaseModel):
    """Configuration for apps that require a login request to obtain a JWT.

    Example nuguard.yaml block::

        auth:
          type: login_flow
          login_flow:
            endpoint: /login
            payload:
              username: ${APP_USERNAME}
              password: ${APP_PASSWORD}
            token_response_key: access_token
            token_header: "Authorization: Bearer"
            refresh_on_401: true
    """

    endpoint: str = "/login"
    method: Literal["POST", "GET"] = "POST"

    # Key/value pairs sent in the login request body.
    # Values are resolved from ${ENV_VAR} by the config loader before this
    # model is built.
    payload: dict[str, Any] = Field(default_factory=dict)

    # JSON key path in the login response that holds the token.
    # Supports dot-notation for nested keys, e.g. "data.token".
    token_response_key: str = "access_token"

    # Header prefix to attach the token value to.
    # e.g. "Authorization: Bearer" or "X-Auth-Token: "
    token_header: str = "Authorization: Bearer"

    # Re-execute the login flow automatically when a 401 is received mid-run.
    refresh_on_401: bool = True


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------

_HTTPONLY_PREFIX = "#HttpOnly_"


def _parse_netscape_cookies(path: str) -> str:
    """Parse a Netscape-format cookies.txt file into a Cookie header value.

    The Netscape cookie format uses 7 tab-separated columns per line::

        domain  include_subdomains  path  secure  expiry  name  value

    Lines starting with '#' are treated as comments and ignored, **except**
    for curl's ``#HttpOnly_<domain>`` prefix which marks HttpOnly cookies —
    those are parsed normally after stripping the prefix.

    The function returns a semicolon-joined ``name=value`` string suitable
    for the ``Cookie:`` HTTP header, e.g. ``"session=abc; csrf=xyz"``.

    Raises:
        FileNotFoundError: if *path* does not exist.
    """
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"auth.cookie_file: file not found at {path!r}. "
            "Generate it with: curl --cookie-jar <file> <login-url>"
        )

    pairs: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            # curl writes HttpOnly cookies with a "#HttpOnly_<domain>" prefix
            # instead of the bare domain — strip it so the line parses normally.
            if line.startswith(_HTTPONLY_PREFIX):
                line = line[len(_HTTPONLY_PREFIX):]
            elif line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 7:  # noqa: PLR2004  (standard Netscape column count)
                name, value = parts[5], parts[6]
                pairs.append(f"{name}={value}")
            else:
                _log.debug(
                    "_parse_netscape_cookies: skipping malformed line in %r: %r",
                    path,
                    line[:80],
                )
    return "; ".join(pairs)


class AuthConfig(BaseModel):
    """Structured auth configuration parsed from nuguard.yaml auth block."""

    type: Literal["bearer", "api_key", "basic", "none", "login_flow", "cookie_file"] = "none"

    # bearer / api_key: full header string e.g. "Authorization: Bearer tok"
    header: str = ""

    # basic: plaintext username and password (resolved from env vars before
    # this model is built)
    username: str = ""
    password: str = ""

    # login_flow: populated when type="login_flow"
    login_flow: LoginFlowConfig | None = None

    # cookie_file: path to a Netscape-format cookies.txt (absolute or relative
    # to the directory where nuguard is invoked)
    cookie_file: str = ""

    @model_validator(mode="after")
    def _validate_fields(self) -> "AuthConfig":
        if self.type == "bearer" and not self.header:
            raise ValueError("auth.type=bearer requires auth.header")
        if self.type == "api_key" and not self.header:
            raise ValueError("auth.type=api_key requires auth.header")
        if self.type == "basic" and not (self.username and self.password):
            raise ValueError("auth.type=basic requires auth.username and auth.password")
        if self.type == "login_flow" and self.login_flow is None:
            raise ValueError("auth.type=login_flow requires auth.login_flow block")
        if self.type == "cookie_file" and not self.cookie_file:
            raise ValueError(
                "auth.type=cookie_file requires auth.cookie_file to be set "
                "to the path of a Netscape-format cookies.txt"
            )
        return self

    def to_headers(self) -> dict[str, str]:
        """Return static HTTP headers to be merged into every request.

        For login_flow auth, returns an empty dict — live headers are managed
        by AuthSession after token acquisition.
        """
        if self.type == "none":
            return {}
        if self.type in ("bearer", "api_key"):
            name, _, value = self.header.partition(":")
            return {name.strip(): value.strip()}
        if self.type == "basic":
            credential = base64.b64encode(
                f"{self.username}:{self.password}".encode()
            ).decode()
            return {"Authorization": f"Basic {credential}"}
        if self.type == "cookie_file":
            cookie_value = _parse_netscape_cookies(self.cookie_file)
            return {"Cookie": cookie_value} if cookie_value else {}
        # login_flow: token not yet acquired — use AuthSession
        return {}

    @classmethod
    def from_header_string(cls, auth_header: str) -> "AuthConfig":
        """Parse the legacy flat auth_header string into an AuthConfig.

        Accepts "Authorization: Bearer tok" or "X-API-Key: key".
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
        """Build an AuthConfig from a canary.json tenant session_token."""
        if not session_token:
            return cls(type="none")
        if ":" in session_token.split()[0]:
            return cls.from_header_string(session_token)
        return cls(type="bearer", header=f"Authorization: Bearer {session_token}")


# ---------------------------------------------------------------------------
# AuthSession — stateful token holder used by behavior and redteam
# ---------------------------------------------------------------------------

def _extract_nested(data: dict[str, Any], key_path: str) -> str | None:
    """Extract a value from a nested dict using dot-notation key path."""
    parts = key_path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return str(current) if current is not None else None


class AuthSession:
    """Stateful auth session shared by behavior and redteam.

    Wraps an AuthConfig and manages token acquisition for login_flow auth.
    For static auth types (bearer, api_key, basic, none) it is a thin
    pass-through over AuthConfig.to_headers().

    Usage::

        session = AuthSession(auth_config, base_url="http://localhost:3001")
        await session.initialize()       # executes login flow if configured

        headers = session.headers()      # attach to every outbound request

        # On receiving HTTP 401:
        if await session.refresh_if_needed():
            retry_with(session.headers())
    """

    def __init__(self, config: AuthConfig, base_url: str) -> None:
        self._config = config
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._token_header_name: str = "Authorization"
        self._token_header_value_prefix: str = "Bearer"

        if config.type == "login_flow" and config.login_flow:
            raw = config.login_flow.token_header
            name, _, prefix = raw.partition(":")
            self._token_header_name = name.strip()
            self._token_header_value_prefix = prefix.strip()

    async def initialize(self) -> None:
        """Acquire a token if using login_flow auth; no-op for static types."""
        if self._config.type == "login_flow":
            await self._do_login()

    def headers(self) -> dict[str, str]:
        """Return HTTP headers for the current auth state.

        For login_flow: returns the live token header (logs a warning and
        returns empty dict if called before initialize()).
        For static types: delegates to AuthConfig.to_headers().
        """
        if self._config.type == "login_flow":
            if self._token is None:
                _log.warning(
                    "AuthSession.headers() called before initialize() — no token"
                )
                return {}
            value = (
                f"{self._token_header_value_prefix} {self._token}".strip()
                if self._token_header_value_prefix
                else self._token
            )
            return {self._token_header_name: value}
        return self._config.to_headers()

    async def refresh_if_needed(self) -> bool:
        """Re-execute login on 401 if the config permits it.

        Returns True if a token refresh was attempted (caller should retry
        the failed request with the updated headers()), False otherwise.
        """
        if (
            self._config.type == "login_flow"
            and self._config.login_flow is not None
            and self._config.login_flow.refresh_on_401
        ):
            _log.info("AuthSession: 401 received — refreshing token via login flow")
            await self._do_login()
            return True
        return False

    async def _do_login(self) -> None:
        """Call the login endpoint and store the extracted token."""
        lf = self._config.login_flow
        assert lf is not None  # always true when type == login_flow

        url = f"{self._base_url}{lf.endpoint}"
        _log.debug("AuthSession: logging in via %s %s", lf.method, url)

        try:
            async with httpx.AsyncClient(timeout=_LOGIN_TIMEOUT) as client:
                if lf.method == "POST":
                    resp = await client.post(url, json=lf.payload)
                else:
                    resp = await client.get(url, params=lf.payload)

            if resp.status_code not in range(200, 300):
                _log.warning(
                    "AuthSession: login failed — HTTP %d from %s: %s",
                    resp.status_code,
                    url,
                    resp.text[:200],
                )
                return

            try:
                body = resp.json()
            except Exception:
                _log.warning(
                    "AuthSession: login response from %s is not JSON: %s",
                    url,
                    resp.text[:200],
                )
                return

            token = _extract_nested(body, lf.token_response_key)
            if not token:
                _log.warning(
                    "AuthSession: token key %r not found in login response from %s: %s",
                    lf.token_response_key,
                    url,
                    str(body)[:200],
                )
                return

            self._token = token
            _log.debug(
                "AuthSession: token acquired from %s (key=%r)",
                url,
                lf.token_response_key,
            )

        except httpx.TimeoutException:
            _log.warning(
                "AuthSession: login request timed out after %ss for %s",
                _LOGIN_TIMEOUT,
                url,
            )
        except httpx.RequestError as exc:
            _log.warning("AuthSession: login request failed for %s: %s", url, exc)
        except Exception as exc:
            _log.warning(
                "AuthSession: unexpected error during login for %s: %s",
                url,
                exc,
            )
