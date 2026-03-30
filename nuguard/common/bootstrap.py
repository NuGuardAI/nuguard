"""Auth bootstrap: verifies every credential against the live target
before any scenario runs. Raises TargetUnavailableError for hard network
failures; returns a TargetHealthReport for auth failures so callers can
decide whether to abort.
"""
from __future__ import annotations

import logging
import time
import uuid

import httpx

from nuguard.common.auth import AuthConfig, AuthSession
from nuguard.common.errors import TargetUnavailableError
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport
from nuguard.redteam.target.canary import CanaryConfig

logger = logging.getLogger(__name__)

# Timeout for the single bootstrap health-check request (intentionally short)
BOOTSTRAP_TIMEOUT = 15.0


class AuthBootstrapper:
    """Verifies connectivity and authentication for all declared credentials.

    Usage::

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
        timeout: float | None = None,
    ) -> None:
        self._target_url = target_url.rstrip("/")
        self._endpoint = endpoint
        self._default_auth = default_auth or AuthConfig(type="none")
        self._canary = canary_config
        self._run_id = run_id or str(uuid.uuid4())
        self._timeout = timeout if timeout is not None else BOOTSTRAP_TIMEOUT
        # Initialised during run() — exposed so validate/redteam can share it
        self._session: AuthSession | None = None

    @property
    def full_url(self) -> str:
        return f"{self._target_url}{self._endpoint}"

    @property
    def session(self) -> AuthSession:
        """The resolved AuthSession for the default credential.

        Available after run() completes.  Both validate and redteam runners
        call bootstrapper.run() before sending any requests, then use
        bootstrapper.session.headers() on every outbound call.

        Raises RuntimeError if accessed before run() is called.
        """
        if self._session is None:
            raise RuntimeError("AuthBootstrapper.session accessed before run()")
        return self._session

    async def run(self) -> TargetHealthReport:
        """Run bootstrap checks for all credentials. Returns a TargetHealthReport.

        Executes the login flow (if configured) before probing the endpoint,
        so the connectivity check uses the acquired token rather than raw
        credentials.

        Does NOT raise on individual auth failures — callers inspect the report
        and decide whether to abort. The only exception raised here is a
        network-level error that prevents the default credential from reaching
        the target.
        """
        # Initialise the AuthSession — executes login flow if configured
        self._session = AuthSession(self._default_auth, self._target_url)
        await self._session.initialize()

        report = TargetHealthReport(
            target_url=self._target_url,
            endpoint=self._endpoint,
            run_id=self._run_id,
        )

        # Always check the default credential first, using the live session
        # headers (which carry the acquired JWT for login_flow auth)
        result = await self._check_one(
            identity="default",
            headers=self._session.headers(),
            auth_type=self._default_auth.type,
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
                    report.checks.append(
                        CredentialCheckResult(
                            identity=tenant.tenant_id,
                            auth_type="skipped",
                            endpoint=self.full_url,
                            status="skipped",
                            error_detail="session_token is empty",
                        )
                    )
                    continue
                tenant_auth = AuthConfig.from_tenant_token(tenant.session_token)
                # Tenant credentials are always static (bearer/api_key) —
                # no login flow needed; use AuthConfig.to_headers() directly
                tenant_result = await self._check_one(
                    identity=tenant.tenant_id,
                    headers=tenant_auth.to_headers(),
                    auth_type=tenant_auth.type,
                )
                report.checks.append(tenant_result)

        return report

    async def _check_one(
        self,
        identity: str,
        headers: dict[str, str],
        auth_type: str,
    ) -> CredentialCheckResult:
        """Send a single health-check POST to the endpoint and record the result.

        Args:
            identity: Human-readable name for the credential being checked.
            headers: Resolved auth headers (from AuthSession or AuthConfig).
            auth_type: Auth type string for reporting (bearer/basic/login_flow/…).
        """
        request_headers = {
            "User-Agent": "nuguard-bootstrap/1.0",
            "Content-Type": "application/json",
            **headers,
        }
        # Minimal well-formed payload — enough to get an auth decision from the server.
        # Does NOT need to produce a meaningful AI response; just needs a 2xx vs 4xx.
        probe_body = {"message": "ping"}
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self.full_url,
                    json=probe_body,
                    headers=request_headers,
                )
            elapsed_ms = (time.monotonic() - start) * 1000

            if 200 <= resp.status_code < 300:
                logger.debug(
                    "bootstrap ok: identity=%s status=%d", identity, resp.status_code
                )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=self.full_url,
                    status="ok",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                )

            if resp.status_code in (401, 403):
                detail = resp.text[:200] if resp.text else ""
                logger.warning(
                    "bootstrap auth_failed: identity=%s status=%d",
                    identity,
                    resp.status_code,
                )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=self.full_url,
                    status="auth_failed",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                    error_detail=detail,
                )

            if 400 <= resp.status_code < 500:
                # Other 4xx (400, 404, 422, …): the server responded, so it is
                # reachable. The probe payload likely doesn't match the API
                # contract — the actual scenario payloads will use the correct
                # format. Treat as connectivity-ok.
                logger.debug(
                    "bootstrap ok (probe format mismatch): identity=%s status=%d",
                    identity,
                    resp.status_code,
                )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=self.full_url,
                    status="ok",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                )

            # 5xx → server is returning errors; treat as target_unavailable
            detail = f"HTTP {resp.status_code}"
            logger.warning(
                "bootstrap target_unavailable: identity=%s %s", identity, detail
            )
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth_type,
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
                auth_type=auth_type,
                endpoint=self.full_url,
                status="target_unavailable",
                response_time_ms=elapsed_ms,
                error_detail=f"timeout after {self._timeout}s: {exc}",
            )
        except httpx.RequestError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth_type,
                endpoint=self.full_url,
                status="target_unavailable",
                response_time_ms=elapsed_ms,
                error_detail=str(exc),
            )
