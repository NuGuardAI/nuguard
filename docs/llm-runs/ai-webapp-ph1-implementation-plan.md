# Phase 1 Implementation Plan: Auth Bootstrap and Schema Verification

## Goal

Prove the platform can authenticate as each declared credential type and exchange valid
requests/responses with the target endpoint before any scenario runs. No findings. No scenarios.
Just connectivity.

**Deliverables:**
1. `redteam.auth` structured block in `nuguard.yaml` (type: `bearer` | `api_key` | `basic` | `none`)
2. `validate` top-level section in `nuguard.yaml` with `target`, `auth`, `workflows`
3. Auth bootstrap health check runs automatically before redteam and validate
4. `TargetHealthReport` — per-credential connectivity result
5. `nuguard target verify` CLI command — standalone connectivity check

**Exit criteria:** `nuguard target verify --config nuguard.yaml` completes without error against a
real target for each supported auth type. Per-tenant `session_token` entries from `canary.json`
are each verified independently.

---

## What Already Exists

| Component | Location | Status |
|---|---|---|
| `NuGuardConfig` + `_flatten_yaml()` | `nuguard/config.py` | Exists; needs `validate.*` and `redteam.auth.*` parsing |
| `TargetAppClient` | `nuguard/redteam/target/client.py` | Exists; takes `default_headers`; has `health_check()` (GET /health) and `invoke_endpoint()` |
| `CanaryConfig` + `CanaryTenant.session_token` | `nuguard/redteam/target/canary.py` | Exists; tenant tokens are the multi-identity source |
| `NuGuardError` base exception | `nuguard/common/errors.py` | Exists; `TargetUnavailableError` needed (may be in client.py) |
| `RedteamOrchestrator` | `nuguard/redteam/executor/orchestrator.py` | Exists; needs bootstrap injected before `run()` |
| CLI structure | `nuguard/cli/main.py` | Exists; `target` sub-app needs registration |
| Auth header parsing | `nuguard/cli/commands/redteam.py` `_run_redteam()` | Exists as inline string split; needs to move to `AuthConfig` |

---

## What Is New

| Component | Location | Purpose |
|---|---|---|
| `AuthConfig` | `nuguard/common/auth.py` | Pydantic model for structured auth; builds request headers |
| `AuthBootstrapper` | `nuguard/common/bootstrap.py` | Verifies each credential against the live target |
| `TargetHealthReport` + `CredentialCheckResult` | `nuguard/models/health_report.py` | Typed result of the bootstrap check |
| `nuguard target verify` command | `nuguard/cli/commands/target.py` | Standalone CLI for connectivity checks |
| `validate.*` config fields | `nuguard/config.py` | `ValidateConfig` model; `_flatten_yaml()` extension |
| `AuthError` exception | `nuguard/common/errors.py` | Raised when credential is invalid; distinct from `TargetUnavailableError` |

---

## Step-by-Step Tasks

### Task 1 — Add `AuthError` and `TargetUnavailableError` to `errors.py`

**File:** `nuguard/common/errors.py`

Add two new exception classes after the existing hierarchy:

```python
class AuthError(NuGuardError):
    """Raised when authentication fails (401/403) during bootstrap or a live run.

    Attributes:
        status_code: HTTP status returned by the target (401, 403, or 0 for pre-request errors).
        identity: Which credential/tenant triggered the failure.
        detail: Raw response body snippet for diagnostics.
    """
    def __init__(
        self,
        message: str,
        status_code: int = 0,
        identity: str = "default",
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.identity = identity
        self.detail = detail


class TargetUnavailableError(NuGuardError):
    """Raised when the target is unreachable (network error, 5xx, circuit breaker).

    Attributes:
        url: The URL that failed.
        cause: Underlying exception or HTTP status that triggered the error.
    """
    def __init__(self, message: str, url: str = "", cause: str = "") -> None:
        super().__init__(message)
        self.url = url
        self.cause = cause
```

`TargetUnavailableError` may already be defined in `client.py` as a local class. If so, move
it to `errors.py` and update the import in `client.py`. Do not duplicate it.

---

### Task 2 — Create `AuthConfig` in `nuguard/common/auth.py`

**New file:** `nuguard/common/auth.py`

```python
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
```

**Tests to add** (`nuguard/common/tests/test_auth_config.py`):

- `test_bearer_to_headers()` — `AuthConfig(type="bearer", header="Authorization: Bearer tok").to_headers()` → `{"Authorization": "Bearer tok"}`
- `test_api_key_to_headers()` — custom header name is preserved
- `test_basic_to_headers()` — base64 encoding is correct for known username/password
- `test_none_to_headers()` — returns `{}`
- `test_from_header_string_bearer()` — parses legacy `auth_header` string
- `test_from_header_string_api_key()` — custom header is typed as `api_key`
- `test_from_header_string_empty()` — returns `type=none`
- `test_from_tenant_token_bare_token()` — bare token becomes `Authorization: Bearer`
- `test_from_tenant_token_header_string()` — header string preserved
- `test_bearer_missing_header_raises()` — validator raises on missing header
- `test_basic_missing_password_raises()` — validator raises on partial credentials

---

### Task 3 — Create `TargetHealthReport` in `nuguard/models/health_report.py`

**New file:** `nuguard/models/health_report.py`

```python
"""Typed result model for auth bootstrap and target connectivity checks."""
from __future__ import annotations

from datetime import datetime, UTC
from typing import Literal

from pydantic import BaseModel, Field


class CredentialCheckResult(BaseModel):
    """Result of one credential verification attempt against the target."""

    identity: str                  # "default", or canary.json tenant_id
    auth_type: str                 # "bearer", "api_key", "basic", "none"
    endpoint: str                  # full URL checked
    status: Literal[
        "ok",                      # 2xx — auth confirmed
        "auth_failed",             # 401 or 403
        "target_unavailable",      # 5xx or network error
        "skipped",                 # session_token empty; skipped intentionally
    ]
    http_status_code: int | None = None
    response_time_ms: float | None = None
    error_detail: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TargetHealthReport(BaseModel):
    """Aggregated result of auth bootstrap for all credentials in a run."""

    target_url: str
    endpoint: str
    run_id: str
    checks: list[CredentialCheckResult] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def all_ok(self) -> bool:
        """True only if every non-skipped check has status='ok'."""
        return all(c.status in ("ok", "skipped") for c in self.checks)

    @property
    def failed_checks(self) -> list[CredentialCheckResult]:
        return [c for c in self.checks if c.status not in ("ok", "skipped")]

    def summary_lines(self) -> list[str]:
        """Human-readable one-line summary per credential for CLI output."""
        lines = []
        icons = {"ok": "✓", "auth_failed": "✗ AUTH", "target_unavailable": "✗ UNAVAILABLE", "skipped": "–"}
        for c in self.checks:
            icon = icons.get(c.status, "?")
            timing = f" ({c.response_time_ms:.0f}ms)" if c.response_time_ms else ""
            detail = f" — {c.error_detail}" if c.error_detail else ""
            lines.append(f"  [{icon}] {c.identity} ({c.auth_type}){timing}{detail}")
        return lines
```

**Tests to add** (`nuguard/common/tests/test_health_report.py`):

- `test_all_ok_true_when_all_checks_pass()`
- `test_all_ok_false_when_one_fails()`
- `test_all_ok_true_when_only_skipped_and_ok()`
- `test_failed_checks_filters_correctly()`
- `test_summary_lines_format()`

---

### Task 4 — Create `AuthBootstrapper` in `nuguard/common/bootstrap.py`

**New file:** `nuguard/common/bootstrap.py`

```python
"""Auth bootstrap: verifies every credential against the live target
before any scenario runs. Raises AuthError or TargetUnavailableError
for hard failures; returns a TargetHealthReport for soft (skipped) results.
"""
from __future__ import annotations

import time
import uuid
import logging
from pathlib import Path

import httpx

from nuguard.common.errors import AuthError, TargetUnavailableError
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport
from nuguard.common.auth import AuthConfig
from nuguard.redteam.target.canary import CanaryConfig

logger = logging.getLogger(__name__)

# Timeout for the single bootstrap health-check request (intentionally short)
BOOTSTRAP_TIMEOUT = 15.0


class AuthBootstrapper:
    """Verifies connectivity and authentication for all declared credentials.

    Usage:
        bootstrapper = AuthBootstrapper(
            target_url="http://localhost:3000",
            endpoint="/chat",
            default_auth=AuthConfig(type="bearer", header="Authorization: Bearer tok"),
            canary_config=canary,          # optional; checks tenant session_tokens too
        )
        report = await bootstrapper.run()
        if not report.all_ok:
            raise AuthError("Bootstrap failed", identity=report.failed_checks[0].identity)
    """

    def __init__(
        self,
        target_url: str,
        endpoint: str = "/chat",
        default_auth: AuthConfig | None = None,
        canary_config: CanaryConfig | None = None,
        run_id: str | None = None,
    ) -> None:
        self._target_url = target_url.rstrip("/")
        self._endpoint = endpoint
        self._default_auth = default_auth or AuthConfig(type="none")
        self._canary = canary_config
        self._run_id = run_id or str(uuid.uuid4())

    @property
    def full_url(self) -> str:
        return f"{self._target_url}{self._endpoint}"

    async def run(self) -> TargetHealthReport:
        """Run bootstrap checks for all credentials. Returns a TargetHealthReport.

        Does NOT raise on individual auth failures — callers inspect the report
        and decide whether to abort. The only exception raised here is a network-level
        error that prevents even the default credential from reaching the target.
        """
        report = TargetHealthReport(
            target_url=self._target_url,
            endpoint=self._endpoint,
            run_id=self._run_id,
        )

        # Always check the default credential first
        result = await self._check_one(
            identity="default",
            auth=self._default_auth,
        )
        report.checks.append(result)

        # Raise immediately if the default credential cannot reach the target at all
        if result.status == "target_unavailable":
            raise TargetUnavailableError(
                f"Target unreachable at {self.full_url}: {result.error_detail}",
                url=self.full_url,
                cause=result.error_detail,
            )

        # Check each tenant session_token from canary.json
        if self._canary:
            for tenant in self._canary.tenants:
                if not tenant.session_token:
                    report.checks.append(CredentialCheckResult(
                        identity=tenant.tenant_id,
                        auth_type="skipped",
                        endpoint=self.full_url,
                        status="skipped",
                        error_detail="session_token is empty",
                    ))
                    continue
                tenant_auth = AuthConfig.from_tenant_token(tenant.session_token)
                result = await self._check_one(
                    identity=tenant.tenant_id,
                    auth=tenant_auth,
                )
                report.checks.append(result)

        return report

    async def _check_one(
        self, identity: str, auth: AuthConfig
    ) -> CredentialCheckResult:
        """Send a single health-check POST to the endpoint and record the result."""
        headers = {
            "User-Agent": "nuguard-bootstrap/1.0",
            "Content-Type": "application/json",
            **auth.to_headers(),
        }
        # Minimal well-formed payload — enough to get an auth decision from the server.
        # Does NOT need to produce a meaningful AI response; just needs a 2xx vs 4xx.
        probe_body = {"message": "ping"}
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=BOOTSTRAP_TIMEOUT) as client:
                resp = await client.post(
                    self.full_url,
                    json=probe_body,
                    headers=headers,
                )
            elapsed_ms = (time.monotonic() - start) * 1000

            if 200 <= resp.status_code < 300:
                logger.debug("bootstrap ok: identity=%s status=%d", identity, resp.status_code)
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth.type,
                    endpoint=self.full_url,
                    status="ok",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                )

            if resp.status_code in (401, 403):
                detail = resp.text[:200] if resp.text else ""
                logger.warning(
                    "bootstrap auth_failed: identity=%s status=%d",
                    identity, resp.status_code,
                )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth.type,
                    endpoint=self.full_url,
                    status="auth_failed",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                    error_detail=detail,
                )

            # 5xx or unexpected status → target_unavailable
            detail = f"HTTP {resp.status_code}"
            logger.warning("bootstrap target_unavailable: identity=%s %s", identity, detail)
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth.type,
                endpoint=self.full_url,
                status="target_unavailable",
                http_status_code=resp.status_code,
                response_time_ms=elapsed_ms,
                error_detail=detail,
            )

        except httpx.TimeoutException as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth.type,
                endpoint=self.full_url,
                status="target_unavailable",
                response_time_ms=elapsed_ms,
                error_detail=f"timeout after {BOOTSTRAP_TIMEOUT}s: {exc}",
            )
        except httpx.RequestError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth.type,
                endpoint=self.full_url,
                status="target_unavailable",
                response_time_ms=elapsed_ms,
                error_detail=str(exc),
            )
```

**Tests to add** (`nuguard/common/tests/test_bootstrap.py`):

Use `respx` (already in dev deps) to mock HTTP responses without a live server.

- `test_default_credential_ok()` — 200 response → `all_ok=True`, one check with `status="ok"`
- `test_default_credential_auth_failed_401()` — 401 → report has `status="auth_failed"`, does NOT raise
- `test_default_credential_auth_failed_403()` — 403 same as 401
- `test_default_credential_target_unavailable_500()` — 500 → raises `TargetUnavailableError`
- `test_default_credential_network_error()` — `httpx.ConnectError` → raises `TargetUnavailableError`
- `test_default_credential_timeout()` — `httpx.TimeoutException` → raises `TargetUnavailableError`
- `test_tenant_token_ok()` — canary with one tenant; token valid → two checks, both `ok`
- `test_tenant_token_skipped_when_empty()` — empty `session_token` → check `status="skipped"`
- `test_tenant_token_auth_failed()` — tenant 401 → check `status="auth_failed"` but no raise
- `test_multi_tenant_partial_failure()` — two tenants; one ok, one auth_failed → `all_ok=False`
- `test_headers_injected_correctly_bearer()` — verify `Authorization: Bearer tok` reaches mock
- `test_headers_injected_correctly_basic()` — verify base64 header reaches mock
- `test_headers_injected_correctly_none()` — no Authorization header in request

---

### Task 5 — Extend `NuGuardConfig` in `nuguard/config.py`

#### 5a. Add `ValidateConfig` model

Add a new Pydantic model after `NuGuardConfig` (or in a separate section at the top of the file,
before `NuGuardConfig`):

```python
class ValidateAuthConfig(BaseModel):
    """Structured auth config for validate mode, parsed from validate.auth block."""
    type: Literal["bearer", "api_key", "basic", "none"] = "none"
    header: str = ""
    username: str = ""
    password: str = ""


class ValidateBoundaryAssertion(BaseModel):
    """Single boundary assertion declared in nuguard.yaml validate.boundary_assertions."""
    name: str
    message: str
    expect: Literal["refused"] = "refused"
    forbid_pattern: str = ""


class ValidateConfig(BaseModel):
    """Configuration for nuguard validate mode (Phase 3 execution; Phase 1 config parsing)."""
    target: str = ""
    target_endpoint: str = "/chat"
    auth: ValidateAuthConfig = Field(default_factory=ValidateAuthConfig)
    canary: str = ""                   # path to canary.json
    workflows: list[str] = Field(default_factory=list)
    capability_map: bool = True
    boundary_assertions: list[ValidateBoundaryAssertion] = Field(default_factory=list)
    request_timeout: float = 60.0
    verbose: bool = False
```

#### 5b. Add `validate_config` field to `NuGuardConfig`

```python
class NuGuardConfig(BaseSettings):
    # ... existing fields ...

    # Validate mode (Phase 1: config parsing; Phase 3: execution)
    validate_config: ValidateConfig = Field(default_factory=ValidateConfig)

    # Structured auth for redteam (complements existing auth_header string)
    redteam_auth_type: str = "none"       # "bearer" | "api_key" | "basic" | "none"
    redteam_auth_username: str = ""
    redteam_auth_password: str = ""
    # (auth_header already exists for bearer/api_key — no change needed there)

    # Defence regressions declared in nuguard.yaml
    redteam_defence_regressions: list[dict] = Field(default_factory=list)
```

#### 5c. Extend `_flatten_yaml()` to parse the new sections

Add the following mappings inside `_flatten_yaml()`:

```python
# ── validate section ──────────────────────────────────────────────────────────
# NOTE: validate.target is stored inside validate_config, NOT written to
# target_url (which is the redteam field). The two modes may point at different
# URLs, so they are kept fully separate.
if "validate" in data:
    v = data.pop("validate")
    if isinstance(v, dict):
        # Preserve the whole validate block as a nested config object.
        # All validate fields (target, auth, workflows, etc.) live on
        # validate_config — callers use cfg.validate_config.target, not
        # cfg.target_url, when operating in validate mode.
        out["validate_config"] = v

# ── redteam.auth structured block ─────────────────────────────────────────────
if "redteam" in data:
    rt = data["redteam"]
    if isinstance(rt, dict) and "auth" in rt:
        auth = rt.pop("auth")
        if isinstance(auth, dict):
            auth_type = auth.get("type", "none")
            out["redteam_auth_type"] = auth_type
            if auth_type in ("bearer", "api_key") and "header" in auth:
                out["redteam_auth_header"] = auth["header"]
            if auth_type == "basic":
                out["redteam_auth_username"] = auth.get("username", "")
                out["redteam_auth_password"] = auth.get("password", "")

    # ── redteam.defence_regressions ───────────────────────────────────────────
    if isinstance(rt, dict) and "defence_regressions" in rt:
        out["redteam_defence_regressions"] = rt.pop("defence_regressions")
```

#### 5d. Add `resolved_auth_config()` and `resolved_validate_auth_config()` to `NuGuardConfig`

Two parallel methods — one per mode. Both build an `AuthConfig` from the parsed config.

```python
def resolved_auth_config(self) -> "AuthConfig":
    """Build an AuthConfig from the resolved *redteam* auth settings.

    Priority: structured auth block > legacy auth_header string > none.
    Import is deferred to avoid circular imports.
    """
    from nuguard.common.auth import AuthConfig

    if self.redteam_auth_type and self.redteam_auth_type != "none":
        return AuthConfig(
            type=self.redteam_auth_type,      # type: ignore[arg-type]
            header=self.redteam_auth_header or "",
            username=self.redteam_auth_username,
            password=self.redteam_auth_password,
        )
    if self.redteam_auth_header:
        return AuthConfig.from_header_string(self.redteam_auth_header)
    return AuthConfig(type="none")


def resolved_validate_auth_config(self) -> "AuthConfig":
    """Build an AuthConfig from the resolved *validate* auth settings.

    Reads from validate_config.auth. Falls back to none if not declared.
    Import is deferred to avoid circular imports.
    """
    from nuguard.common.auth import AuthConfig

    va = self.validate_config.auth
    if va.type and va.type != "none":
        return AuthConfig(
            type=va.type,           # type: ignore[arg-type]
            header=va.header,
            username=va.username,
            password=va.password,
        )
    return AuthConfig(type="none")
```

**Tests to add** (`nuguard/tests/test_config.py` — extend existing file):

- `test_flatten_yaml_validate_section()` — YAML with `validate.target` → `validate_config.target` populated; `target_url` (redteam) is NOT overwritten
- `test_flatten_yaml_validate_and_redteam_independent()` — both sections present; each has its own target URL; no cross-contamination
- `test_flatten_yaml_redteam_auth_bearer()` — structured bearer block → `redteam_auth_header` set, `redteam_auth_type="bearer"`
- `test_flatten_yaml_redteam_auth_basic()` — basic block → `redteam_auth_username`, `redteam_auth_password` set
- `test_flatten_yaml_redteam_auth_legacy_string()` — old `auth_header` flat string still works
- `test_flatten_yaml_defence_regressions()` — list of dicts parsed correctly
- `test_resolved_auth_config_bearer()` — structured bearer config → `AuthConfig.type="bearer"`
- `test_resolved_auth_config_basic()` — basic → headers contain `Authorization: Basic ...`
- `test_resolved_auth_config_fallback_to_header_string()` — no structured auth, has `auth_header` → parsed as `bearer`
- `test_resolved_auth_config_none()` — no auth configured → `AuthConfig.type="none"`, empty headers
- `test_resolved_validate_auth_config_bearer()` — validate.auth.type=bearer → `AuthConfig.type="bearer"`
- `test_resolved_validate_auth_config_none()` — no validate auth → `AuthConfig.type="none"`

---

### Task 6 — Integrate Bootstrap into `RedteamOrchestrator`

**File:** `nuguard/redteam/executor/orchestrator.py`

#### 6a. Add `auth_config` parameter to constructor

```python
# Add to __init__ signature:
auth_config: "AuthConfig | None" = None,
```

Store as `self._auth_config = auth_config`.

#### 6b. Update header building in `run()`

The existing `run()` builds `extra_headers` from the raw `auth_header` string inline.
Replace that with:

```python
# At the top of run(), before TargetAppClient instantiation:
from nuguard.common.auth import AuthConfig
effective_auth = self._auth_config or AuthConfig(type="none")
extra_headers = effective_auth.to_headers()
```

#### 6c. Add bootstrap step at the start of `run()`

```python
# After resolving extra_headers, before creating TargetAppClient:
from nuguard.common.bootstrap import AuthBootstrapper
bootstrapper = AuthBootstrapper(
    target_url=self._target_url,
    endpoint=self._chat_path,
    default_auth=effective_auth,
    canary_config=self._canary_config,
    run_id=str(uuid.uuid4()),
)
health_report = await bootstrapper.run()
# Log the bootstrap result
for line in health_report.summary_lines():
    logger.info("bootstrap %s", line)
# Store for reporting
self.health_report: TargetHealthReport = health_report
# Abort on default credential failure — do not run scenarios with broken auth
if health_report.failed_checks:
    failed = health_report.failed_checks[0]
    if failed.status == "auth_failed":
        from nuguard.common.errors import AuthError
        raise AuthError(
            f"Auth failed for identity '{failed.identity}' "
            f"(HTTP {failed.http_status_code}): {failed.error_detail}",
            status_code=failed.http_status_code or 0,
            identity=failed.identity,
            detail=failed.error_detail,
        )
    # target_unavailable is already raised inside AuthBootstrapper.run()
```

#### 6d. Update `_run_redteam()` in `nuguard/cli/commands/redteam.py`

Pass the resolved `AuthConfig` into `RedteamOrchestrator`:

```python
auth_config = cfg.resolved_auth_config()

orchestrator = RedteamOrchestrator(
    ...existing args...,
    auth_config=auth_config,
    extra_headers=auth_config.to_headers(),   # keep for backward compat
)
```

Remove the inline `auth_header` string-split logic that currently builds `extra_headers`
directly — it is now handled by `AuthConfig`.

---

### Task 7 — Create `nuguard target verify` CLI Command

**New file:** `nuguard/cli/commands/target.py`

```python
"""nuguard target — target connectivity and auth verification commands."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from nuguard.config import load_config
from nuguard.common.errors import TargetUnavailableError
from nuguard.common.auth import AuthConfig
from nuguard.common.bootstrap import AuthBootstrapper
from nuguard.models.health_report import TargetHealthReport
from nuguard.redteam.target.canary import CanaryConfig

target_app = typer.Typer(name="target", help="Target connectivity and auth verification.")
console = Console()


@target_app.command(name="verify")
def verify_command(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to nuguard.yaml"),
    ] = None,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Which config section to verify: redteam or validate"),
    ] = "redteam",
    target: Annotated[
        str | None,
        typer.Option("--target", help="Target base URL (overrides nuguard.yaml)"),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Chat endpoint path (overrides nuguard.yaml)"),
    ] = None,
    auth_header: Annotated[
        str | None,
        typer.Option("--auth-header", help="Auth header string e.g. 'Authorization: Bearer tok'"),
    ] = None,
    canary: Annotated[
        Path | None,
        typer.Option("--canary", help="Path to canary.json (verifies tenant tokens too)"),
    ] = None,
) -> None:
    """Verify authentication and connectivity against the target AI application.

    Sends a single probe request for each declared credential (default + canary tenants)
    and reports HTTP status, response time, and auth result. Exits non-zero if any
    non-skipped credential fails.

    Use --mode to select which nuguard.yaml section drives the check:
      --mode redteam   (default) reads redteam.target and redteam.auth
      --mode validate  reads validate.target and validate.auth

    Examples:
        nuguard target verify
        nuguard target verify --mode validate
        nuguard target verify --target http://localhost:3000 --auth-header "Authorization: Bearer $TOKEN"
        nuguard target verify --config nuguard.yaml --canary canary.json
    """
    if mode not in ("redteam", "validate"):
        console.print(f"[red]Error:[/red] --mode must be 'redteam' or 'validate', got '{mode}'")
        raise typer.Exit(code=1)
    asyncio.run(_verify_async(config, mode, target, endpoint, auth_header, canary))


async def _verify_async(
    config_path: Path | None,
    mode: str,
    target_override: str | None,
    endpoint_override: str | None,
    auth_header_override: str | None,
    canary_path: Path | None,
) -> None:
    # Load config
    cfg = load_config(config_path)

    # Resolve target URL and auth from the selected mode section
    if mode == "validate":
        target_url = target_override or cfg.validate_config.target
        ep = endpoint_override or cfg.validate_config.target_endpoint or "/chat"
        if auth_header_override:
            auth = AuthConfig.from_header_string(auth_header_override)
        else:
            auth = cfg.resolved_validate_auth_config()
        mode_label = "validate"
    else:
        target_url = target_override or cfg.target_url
        ep = endpoint_override or cfg.target_endpoint or "/chat"
        if auth_header_override:
            auth = AuthConfig.from_header_string(auth_header_override)
        else:
            auth = cfg.resolved_auth_config()
        mode_label = "redteam"

    if not target_url:
        section = "validate.target" if mode == "validate" else "redteam.target"
        console.print(f"[red]Error:[/red] No target URL. Set {section} in nuguard.yaml or pass --target.")
        raise typer.Exit(code=1)

    # Load canary (for tenant token verification)
    # Prefer explicit --canary flag, then mode-specific config, then top-level canary_path.
    canary_config: CanaryConfig | None = None
    if canary_path:
        canary_file: Path | None = canary_path
    elif mode == "validate" and cfg.validate_config.canary:
        canary_file = Path(cfg.validate_config.canary)
    elif cfg.canary_path:
        canary_file = Path(cfg.canary_path)
    else:
        canary_file = None

    if canary_file and canary_file.exists():
        canary_config = CanaryConfig.load(canary_file)
    elif canary_file:
        console.print(f"[yellow]Warning:[/yellow] canary file not found: {canary_file}")

    console.print(f"\n[bold]NuGuard Target Verify[/bold]  (mode: {mode_label})")
    console.print(f"  Target:   {target_url}{ep}")
    console.print(f"  Auth:     {auth.type}")
    if canary_config:
        tenant_count = len([t for t in canary_config.tenants if t.session_token])
        console.print(f"  Tenants:  {tenant_count} with session_token in canary.json")
    console.print()

    # Run bootstrap
    try:
        bootstrapper = AuthBootstrapper(
            target_url=target_url,
            endpoint=ep,
            default_auth=auth,
            canary_config=canary_config,
        )
        report: TargetHealthReport = await bootstrapper.run()
    except TargetUnavailableError as exc:
        console.print(f"[red]✗ Target unavailable:[/red] {exc}")
        raise typer.Exit(code=2)

    # Render results table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Identity", style="cyan")
    table.add_column("Auth Type")
    table.add_column("Status")
    table.add_column("HTTP", justify="right")
    table.add_column("Time (ms)", justify="right")
    table.add_column("Detail")

    status_styles = {
        "ok": "green",
        "auth_failed": "red",
        "target_unavailable": "red",
        "skipped": "dim",
    }

    for check in report.checks:
        style = status_styles.get(check.status, "white")
        table.add_row(
            check.identity,
            check.auth_type,
            f"[{style}]{check.status}[/{style}]",
            str(check.http_status_code) if check.http_status_code else "—",
            f"{check.response_time_ms:.0f}" if check.response_time_ms else "—",
            check.error_detail[:60] if check.error_detail else "",
        )

    console.print(table)

    if report.all_ok:
        console.print("\n[green]All credentials verified successfully.[/green]")
        raise typer.Exit(code=0)
    else:
        failed = report.failed_checks
        console.print(f"\n[red]{len(failed)} credential(s) failed verification.[/red]")
        for f in failed:
            if f.status == "auth_failed":
                console.print(
                    f"  → [cyan]{f.identity}[/cyan]: authentication rejected "
                    f"(HTTP {f.http_status_code}). Check credentials."
                )
            else:
                console.print(
                    f"  → [cyan]{f.identity}[/cyan]: target unreachable. "
                    f"Is the app running at {target_url}?"
                )
        raise typer.Exit(code=1)
```

**Register in `nuguard/cli/main.py`:**

```python
from nuguard.cli.commands.target import target_app
app.add_typer(target_app, name="target")
```

**Tests to add** (`nuguard/cli/tests/test_target_command.py`):

Use `typer.testing.CliRunner` + `respx` to mock HTTP.

- `test_verify_ok_bearer()` — mock 200, `--auth-header "Authorization: Bearer tok"` → exit 0
- `test_verify_auth_failed()` — mock 401 → exit 1, prints "auth_failed"
- `test_verify_target_unavailable()` — mock network error → exit 2
- `test_verify_with_canary_tenants()` — mock 200 for all tokens → exit 0, table shows all tenants
- `test_verify_no_target_exits_1()` — no target in redteam config, no --target flag → exit 1 with "redteam.target" hint
- `test_verify_canary_file_not_found_warns()` — warns but continues with default credential
- `test_verify_mode_validate_reads_validate_target()` — `--mode validate`; mock 200 against `validate.target` URL → exit 0
- `test_verify_mode_validate_no_target_exits_1()` — `--mode validate` with no validate.target → exit 1 with "validate.target" hint
- `test_verify_mode_invalid_exits_1()` — `--mode bogus` → exit 1 with usage error

---

### Task 9 — Add `nuguard validate` CLI Stub

**New file:** `nuguard/cli/commands/validate.py`

Register the `validate` sub-command entry point now so the CLI tree is consistent from Phase 1. Phase 3 replaces the stub body with the real implementation. This prevents any external tooling or CI scripts that call `nuguard validate` from getting a "no such command" error after Phase 1 ships.

```python
"""nuguard validate — happy-path and policy compliance runner (Phase 3)."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

validate_app = typer.Typer(name="validate", help="Validate AI application behaviour (Phase 3).")
console = typer.get_text_stream("stdout")


@validate_app.callback(invoke_without_command=True)
def validate_command(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to nuguard.yaml"),
    ] = None,
) -> None:
    """Validate AI application happy-path behaviour and cognitive policy compliance.

    Run capability probes, happy-path simulations, boundary assertions, and
    per-turn policy evaluations against the declared target.

    \b
    Phase 3 feature — not yet implemented.
    Use 'nuguard target verify' to check connectivity and auth now.
    """
    if ctx.invoked_subcommand is None:
        typer.echo(
            "nuguard validate is planned for Phase 3.\n"
            "Run 'nuguard target verify' to verify connectivity and authentication."
        )
        raise typer.Exit(code=0)
```

**Register in `nuguard/cli/main.py`:**

```python
from nuguard.cli.commands.validate import validate_app
app.add_typer(validate_app, name="validate")
```

**Tests to add** (`nuguard/cli/tests/test_validate_command.py`):

- `test_validate_stub_exits_0()` — `nuguard validate` → exit 0, prints Phase 3 message
- `test_validate_help_works()` — `nuguard validate --help` → exit 0, shows help text

---

### Task 8 — Update `__init__.py` Exports

**File:** `nuguard/common/__init__.py`

Add public exports:

```python
from nuguard.common.auth import AuthConfig
from nuguard.common.bootstrap import AuthBootstrapper

__all__ = [
    "AuthConfig",
    "AuthBootstrapper",
    # existing exports...
]
```

**File:** `nuguard/models/__init__.py`

```python
from nuguard.models.health_report import TargetHealthReport, CredentialCheckResult

__all__ = [
    "TargetHealthReport",
    "CredentialCheckResult",
    # existing exports...
]
```

---

## File Change Summary

| File | Change Type | Description |
|---|---|---|
| `nuguard/common/errors.py` | **Modify** | Add `AuthError`, move or add `TargetUnavailableError` |
| `nuguard/common/auth.py` | **Create** | `AuthConfig` model + `to_headers()` + factory methods |
| `nuguard/common/bootstrap.py` | **Create** | `AuthBootstrapper` with `run()` and `_check_one()` |
| `nuguard/models/health_report.py` | **Create** | `TargetHealthReport` + `CredentialCheckResult` |
| `nuguard/config.py` | **Modify** | Add `ValidateConfig`; extend `_flatten_yaml()`; add `resolved_auth_config()` and `resolved_validate_auth_config()` |
| `nuguard/redteam/executor/orchestrator.py` | **Modify** | Accept `auth_config`, run bootstrap at start of `run()` |
| `nuguard/cli/commands/redteam.py` | **Modify** | Pass `AuthConfig` to orchestrator; remove inline header split |
| `nuguard/cli/commands/target.py` | **Create** | `nuguard target verify` with `--mode redteam\|validate` |
| `nuguard/cli/commands/validate.py` | **Create** | `nuguard validate` Phase 3 stub |
| `nuguard/cli/main.py` | **Modify** | Register `target_app` and `validate_app` |
| `nuguard/common/__init__.py` | **Modify** | Export `AuthConfig`, `AuthBootstrapper` |
| `nuguard/models/__init__.py` | **Modify** | Export `TargetHealthReport`, `CredentialCheckResult` |

---

## Test File Summary

| Test File | Tests |
|---|---|
| `nuguard/common/tests/test_auth_config.py` | 11 unit tests for `AuthConfig` |
| `nuguard/common/tests/test_health_report.py` | 5 unit tests for `TargetHealthReport` |
| `nuguard/common/tests/test_bootstrap.py` | 13 tests using `respx` mocks |
| `nuguard/tests/test_config.py` | 11 new tests added to existing file (9 original + 2 new for validate) |
| `nuguard/cli/tests/test_target_command.py` | 9 CLI integration tests using `CliRunner` |
| `nuguard/cli/tests/test_validate_command.py` | 2 stub smoke tests |

**Total: 51 new tests**

Run with:
```bash
uv run pytest nuguard/common/tests/test_auth_config.py \
              nuguard/common/tests/test_health_report.py \
              nuguard/common/tests/test_bootstrap.py \
              nuguard/tests/test_config.py \
              nuguard/cli/tests/test_target_command.py \
              nuguard/cli/tests/test_validate_command.py -v
```

---

## Dependency Check

No new runtime dependencies are required:

| Need | Already available |
|---|---|
| Async HTTP requests | `httpx` (in `pyproject.toml`) |
| HTTP mocking in tests | `respx` (in dev deps) |
| CLI framework | `typer` (in `pyproject.toml`) |
| Rich table output | `rich` (in `pyproject.toml`) |
| Pydantic models | `pydantic` (in `pyproject.toml`) |

---

## Implementation Order

Implement tasks in this order to avoid forward-reference issues:

```
Task 1  errors.py                    (no deps on new code)
Task 2  auth.py                      (depends on: nothing new)
Task 3  health_report.py             (depends on: nothing new)
Task 4  bootstrap.py                 (depends on: auth.py, health_report.py, errors.py)
Task 5  config.py                    (depends on: auth.py — deferred import to avoid circular)
Task 6  orchestrator.py              (depends on: auth.py, bootstrap.py, config.py)
Task 7  target.py + main.py          (depends on: all above; adds --mode validate support)
Task 9  validate.py stub + main.py   (depends on: nothing new; stub only)
Task 8  __init__.py exports          (last — wire up after all files exist)
```

Write tests alongside each task, not after. Each task's test file should pass before
moving to the next task.

---

## Exit Criteria

All of the following must pass before Phase 1 is complete:

1. `uv run pytest nuguard/common/tests/test_auth_config.py -v` — all 11 pass
2. `uv run pytest nuguard/common/tests/test_health_report.py -v` — all 5 pass
3. `uv run pytest nuguard/common/tests/test_bootstrap.py -v` — all 13 pass
4. `uv run pytest nuguard/tests/test_config.py -v` — all existing + 11 new pass
5. `uv run pytest nuguard/cli/tests/test_target_command.py -v` — all 9 pass
6. `uv run pytest nuguard/cli/tests/test_validate_command.py -v` — all 2 pass
7. `uv run ruff check nuguard/` — no new lint errors
8. `uv run mypy nuguard/` — no new type errors in touched files
9. Manual smoke test — `nuguard target verify --target http://localhost:3000 --auth-header "Authorization: Bearer $TOKEN"` exits 0 against a real running target
10. Manual smoke test — `nuguard target verify --mode validate --config nuguard.yaml` exits 0 when `validate.target` is reachable
11. `nuguard validate` exits 0 with Phase 3 stub message (not "command not found")
12. All existing tests (`uv run pytest tests/ -v`) continue to pass — no regressions
