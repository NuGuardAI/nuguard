"""Auth bootstrap: verifies every credential against the live target
before any scenario runs. Raises TargetUnavailableError for hard network
failures; returns a TargetHealthReport for auth failures so callers can
decide whether to abort.
"""
from __future__ import annotations

import time
import uuid

import httpx

from nuguard.common.auth import AuthConfig, AuthSession
from nuguard.common.errors import TargetUnavailableError
from nuguard.common.logging import get_logger
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport
from nuguard.redteam.target.canary import CanaryConfig

logger = get_logger(__name__)

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
        probe_payload_extras: dict[str, object] | None = None,
    ) -> None:
        self._target_url = target_url.rstrip("/")
        self._endpoint = endpoint
        self._default_auth = default_auth or AuthConfig(type="none")
        self._canary = canary_config
        self._run_id = run_id or str(uuid.uuid4())
        self._timeout = timeout if timeout is not None else BOOTSTRAP_TIMEOUT
        self._probe_payload_extras: dict[str, object] = probe_payload_extras or {}
        # Initialised during run() — exposed so behavior/redteam can share it
        self._session: AuthSession | None = None
        # Static headers discovered by the login_flow fallback (HTTP Basic auth
        # built from the original username/password) when it succeeds. Empty
        # unless that fallback fired. Callers must merge these into every
        # outbound request for the rest of the session — session.headers()
        # stays empty since the session's auth type is still login_flow.
        self._fallback_headers: dict[str, str] = {}

    @property
    def full_url(self) -> str:
        return f"{self._target_url}{self._endpoint}"

    @property
    def session(self) -> AuthSession:
        """The resolved AuthSession for the default credential.

        Available after run() completes.  Both behavior and redteam runners
        call bootstrapper.run() before sending any requests, then build
        outbound headers from bootstrapper.session.headers() merged with
        bootstrapper.fallback_headers (the latter is empty unless the
        login_flow→chat-endpoint fallback fired; see that property).

        Raises RuntimeError if accessed before run() is called.
        """
        if self._session is None:
            raise RuntimeError("AuthBootstrapper.session accessed before run()")
        return self._session

    @property
    def fallback_headers(self) -> dict[str, str]:
        """Headers discovered by the login_flow→chat-endpoint fallback, if any.

        Empty unless ``run()`` fell back from a broken login_flow to sending the
        original username/password as HTTP Basic auth directly to the chat
        endpoint and that worked. Callers must merge this into the headers used
        for every request, not just the bootstrap probe.
        """
        return dict(self._fallback_headers)

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

        # If login_flow auth was configured but token acquisition failed, the SBOM's
        # declared auth endpoint isn't usable. If this config came from a "basic"
        # username/password that we upgraded to login_flow, fall back to sending
        # those same credentials as HTTP Basic auth straight to the chat endpoint
        # rather than failing outright. If that also fails, report the real login
        # failure reason instead of a generic message.
        login_flow_failed = (
            self._default_auth.type == "login_flow" and not self._session.login_succeeded
        )
        fallback_headers: dict[str, str] = {}
        if login_flow_failed:
            if self._default_auth.username and self._default_auth.password:
                fallback_headers = AuthConfig(
                    type="basic",
                    username=self._default_auth.username,
                    password=self._default_auth.password,
                ).to_headers()
            logger.warning(
                "bootstrap: login_flow failed (%s) — falling back to %s directly to %s",
                self._session.login_error,
                "the original username/password (HTTP Basic)" if fallback_headers else "no auth",
                self.full_url,
            )

        # Always check the default credential. Use the fallback headers when the
        # login flow failed; otherwise the live session headers (the acquired JWT
        # for login_flow auth, or the static headers for other auth types).
        result = await self._check_one(
            identity="default",
            headers=fallback_headers if login_flow_failed else self._session.headers(),
            auth_type=self._default_auth.type,
        )

        if login_flow_failed:
            if result.status == "ok":
                logger.info(
                    "bootstrap: chat endpoint accepted %s — continuing with that, "
                    "won't retry the login endpoint again this session",
                    "the original username/password directly" if fallback_headers else "the request without a token",
                )
                self._session.mark_login_flow_unusable()
                self._fallback_headers = fallback_headers
            else:
                result = CredentialCheckResult(
                    identity="default",
                    auth_type=self._default_auth.type,
                    endpoint=self.full_url,
                    status=result.status,
                    http_status_code=result.http_status_code,
                    response_time_ms=result.response_time_ms,
                    error_detail=(
                        f"login_flow token acquisition failed ({self._session.login_error}); "
                        f"fallback probe to chat endpoint also failed: {result.error_detail}"
                    ),
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
        # Does NOT need to produce a meaningful AI response; just needs a 2xx vs 4xx/5xx.
        # Extra static fields (chat_payload_extras) are merged in so apps that crash on
        # missing required fields (e.g. vehicleState) don't trip the target_unavailable check.
        probe_body = {**self._probe_payload_extras, "message": "ping"}
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
                    response_text=resp.text[:500] if resp.text else "",
                )

            if resp.status_code in (401, 403):
                detail = resp.text[:200] if resp.text else ""
                content_type = resp.headers.get("content-type", "")
                body_prefix = (resp.text or "").lstrip()[:15].lower()
                body_is_html = (
                    "text/html" in content_type
                    or body_prefix.startswith("<!doctype")
                    or body_prefix.startswith("<html")
                )
                # A 403 with an HTML body is a hosting-platform error page (e.g. Azure
                # "Web App - Unavailable", Cloudflare gateway error) — not a real auth
                # rejection.  Route it to target_unavailable so callers get an accurate
                # "service is down" message rather than a false "bad credentials" error.
                if resp.status_code == 403 and body_is_html:
                    logger.warning(
                        "bootstrap target_unavailable (403 HTML page): identity=%s — "
                        "server returned an HTML error page, service may be down",
                        identity,
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

            if resp.status_code == 500:
                # HTTP 500 means the server ran but crashed on our minimal probe body
                # (e.g. a missing required field triggers a JS/Python TypeError).
                # The server IS reachable — treat it the same as 422: payload mismatch,
                # not an infrastructure failure.  Actual scenario payloads use the full
                # chat_payload_extras and will produce meaningful responses.
                logger.debug(
                    "bootstrap ok (500 — likely probe payload mismatch): identity=%s",
                    identity,
                )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=self.full_url,
                    status="ok",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                )

            # 502/503/504/5xx → gateway or server is down; treat as target_unavailable
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
