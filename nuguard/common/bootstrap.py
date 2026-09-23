"""Auth bootstrap: verifies every credential against the live target
before any scenario runs. Raises TargetUnavailableError for hard network
failures; returns a TargetHealthReport for auth failures so callers can
decide whether to abort.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

import httpx

from nuguard.common.auth import AuthConfig, AuthSession
from nuguard.common.errors import TargetUnavailableError
from nuguard.common.logging import get_logger
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport

if TYPE_CHECKING:
    from pathlib import Path

    from nuguard.common.auth_recovery import BrowserAuthRecovery

    # Deferred: nuguard.redteam.target.canary imports nuguard.common.logging,
    # which would re-enter nuguard.common.__init__ mid-import (it pulls in this
    # module via auth_runtime) if imported eagerly here. Safe to defer since
    # CanaryConfig is only ever used as a type annotation and this module has
    # `from __future__ import annotations`.
    from nuguard.redteam.target.canary import CanaryConfig

logger = get_logger(__name__)

# Timeout for a single bootstrap health-check request.
# 30 s accommodates serverless targets (Azure Container Apps, AWS Lambda) whose
# first request is held while the container cold-starts.
BOOTSTRAP_TIMEOUT = 30.0

# Retries for transient target_unavailable results (connection refused, timeout,
# 502/503/504).  Cold-start containers can fail the first 1-2 probes; retrying
# with backoff avoids a spurious "target is unreachable" hard abort.
BOOTSTRAP_STARTUP_RETRIES = 3

# Backoff base (seconds).  Retry delays: 2 s, 4 s, 8 s.
_BACKOFF_BASE = 2.0


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
        startup_retries: int | None = None,
        is_websocket: bool = False,
        ws_auth_message: dict[str, object] | None = None,
        config_path: "Path | None" = None,
    ) -> None:
        self._target_url = target_url.rstrip("/")
        self._endpoint = endpoint
        self._default_auth = default_auth or AuthConfig(type="none")
        self._canary = canary_config
        self._run_id = run_id or str(uuid.uuid4())
        self._timeout = timeout if timeout is not None else BOOTSTRAP_TIMEOUT
        self._startup_retries = startup_retries if startup_retries is not None else BOOTSTRAP_STARTUP_RETRIES
        self._probe_payload_extras: dict[str, object] = probe_payload_extras or {}
        # When True, _probe_once() opens a WebSocket handshake instead of sending
        # an HTTP POST — see resolve_target_session()'s pre-bootstrap WS detection.
        self._is_websocket = is_websocket
        self._ws_auth_message = ws_auth_message
        # Path to the loaded nuguard.yaml, when known. Passed through to
        # attempt_browser_auth_recovery() so a successful browser-login
        # recovery can be persisted back into the file (see run()); None
        # disables persistence but not recovery itself — the recovered
        # session is still used for this run.
        self._config_path = config_path
        # Initialised during run() — exposed so behavior/redteam can share it
        self._session: AuthSession | None = None
        # Chat-payload fields (e.g. an opaque consumerID) a browser-login
        # recovery's chat-sniff step confirmed the app's own UI sends —
        # populated by _maybe_recover_via_browser() when recovery succeeds.
        # Callers (see resolve_target_session) merge this into
        # chat_payload_extras at the lowest precedence, same as
        # login_response_extras.
        self.discovered_chat_payload_extras: dict[str, str] = {}

    @property
    def full_url(self) -> str:
        return f"{self._target_url}{self._endpoint}"

    @property
    def session(self) -> AuthSession:
        """The resolved AuthSession for the default credential.

        Available after run() completes.  Both behavior and redteam runners
        call bootstrapper.run() before sending any requests, then use
        bootstrapper.session.headers() on every outbound call. If the
        login_flow endpoint proved broken and a fallback probe succeeded,
        run() has already swapped the session's auth config so headers()
        returns the working fallback credentials.

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

        # If login_flow auth was configured but token acquisition failed, the SBOM's
        # declared auth endpoint isn't usable. If this config came from a "basic"
        # username/password that we upgraded to login_flow, fall back to sending
        # those same credentials as HTTP Basic auth straight to the chat endpoint
        # rather than failing outright. If that also fails, report the real login
        # failure reason instead of a generic message.
        login_flow_failed = (
            self._default_auth.type == "login_flow"
            and not self._session.login_succeeded
        )
        fallback_auth_config: AuthConfig | None = None
        if login_flow_failed:
            if self._default_auth.username and self._default_auth.password:
                fallback_auth_config = AuthConfig(
                    type="basic",
                    username=self._default_auth.username,
                    password=self._default_auth.password,
                )
            else:
                fallback_auth_config = AuthConfig(type="none")
            logger.warning(
                "bootstrap: login_flow failed (%s) — falling back to %s directly to %s",
                self._session.login_error,
                (
                    "the original username/password (HTTP Basic)"
                    if fallback_auth_config.type == "basic"
                    else "no auth"
                ),
                self.full_url,
            )

        # Always check the default credential. Use the fallback headers when the
        # login flow failed; otherwise the live session headers (the acquired JWT
        # for login_flow auth, or the static headers for other auth types).
        # auth_type reflects what was actually sent on the wire for this probe —
        # the fallback type (basic/none), not the originally-configured login_flow.
        probe_auth_type = (
            fallback_auth_config.type
            if fallback_auth_config is not None
            else self._default_auth.type
        )
        result = await self._check_one(
            identity="default",
            headers=(
                fallback_auth_config.to_headers()
                if fallback_auth_config is not None
                else self._session.headers()
            ),
            auth_type=probe_auth_type,
        )

        if login_flow_failed:
            assert fallback_auth_config is not None
            if result.status == "ok":
                logger.info(
                    "bootstrap: chat endpoint accepted %s — continuing with that, "
                    "won't retry the login endpoint again this session",
                    (
                        "the original username/password directly"
                        if fallback_auth_config.type == "basic"
                        else "the request without a token"
                    ),
                )
                self._session.replace_config(fallback_auth_config)
            else:
                result = CredentialCheckResult(
                    identity="default",
                    auth_type=probe_auth_type,
                    endpoint=self.full_url,
                    status=result.status,
                    http_status_code=result.http_status_code,
                    response_time_ms=result.response_time_ms,
                    error_detail=(
                        f"login_flow token acquisition failed ({self._session.login_error}); "
                        f"fallback probe to chat endpoint also failed: {result.error_detail}"
                    ),
                )
        # Two situations warrant a real-browser recovery attempt before giving
        # up: (1) a static probe (basic / login_flow, incl. the fallback
        # above) rejected the credential outright — rescues apps that
        # authenticate via an interactive redirect/OAuth flow (e.g. Auth0
        # Universal Login) a plain HTTP client can't drive; (2) the probe got
        # a 2xx but body_warning flagged an empty/unparseable body — likely a
        # missing required payload field the SBOM never captured (common when
        # it only saw the frontend's SPA route, not the real backend API).
        # Neither requires the user to remember to run
        # 'nuguard target discover-browser' by hand first. No-ops (returns
        # None) when the 'browser' extra isn't installed or the login itself
        # fails, so this never blocks a run that would have failed the same
        # way before this existed.
        recovery_reason = (
            "auth_failed" if result.status == "auth_failed"
            else "empty_body" if result.status == "ok" and result.body_warning
            else None
        )
        if recovery_reason is not None:
            recovered = await self._maybe_recover_via_browser(reason=recovery_reason)
            if recovered is not None:
                self.discovered_chat_payload_extras = dict(recovered.chat_payload_extras)
                self._session.replace_config(recovered.auth_config)
                # Re-probe with any newly-discovered payload fields merged in so
                # the report reflects whether recovery actually fixed the
                # request, not just whether the browser login itself succeeded.
                self._probe_payload_extras = {
                    **self._probe_payload_extras,
                    **recovered.chat_payload_extras,
                }
                retry_result = await self._check_one(
                    identity="default",
                    headers=recovered.auth_config.to_headers(),
                    auth_type=recovered.auth_config.type,
                )
                if retry_result.status == "ok" and not retry_result.body_warning:
                    result = retry_result
                else:
                    logger.warning(
                        "bootstrap: browser-login recovery captured a session but the "
                        "retry probe still %s: %s",
                        "failed" if retry_result.status != "ok" else "had an empty/unparseable body",
                        retry_result.error_detail or retry_result.body_warning,
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

    async def _maybe_recover_via_browser(
        self, *, reason: str
    ) -> "BrowserAuthRecovery | None":
        """Attempt browser-login recovery for the default credential.

        Delegates to :func:`nuguard.common.auth_recovery.attempt_browser_auth_recovery`,
        which only acts on basic/login_flow configs carrying username+password
        and returns ``None`` on any failure (missing 'browser' extra, login
        failure, etc.) — this wrapper exists purely to keep that import lazy
        and swallow unexpected errors so recovery can never crash a run that
        would otherwise just report the original probe result as before.
        """
        try:
            from nuguard.common.auth_recovery import attempt_browser_auth_recovery
        except Exception:
            return None
        try:
            return await attempt_browser_auth_recovery(
                target_url=self._target_url,
                auth_config=self._default_auth,
                config_path=self._config_path,
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001 — recovery must never crash bootstrap
            logger.warning("bootstrap: browser-login recovery attempt errored: %s", exc)
            return None

    async def _check_one(
        self,
        identity: str,
        headers: dict[str, str],
        auth_type: str,
    ) -> CredentialCheckResult:
        """Probe the endpoint with retry/backoff for transient target_unavailable results.

        Retries up to ``self._startup_retries`` times when the target returns a
        transient failure (connection error, timeout, 502/503/504).  Auth failures
        (401/403) are never retried — they indicate a credential problem, not a
        cold-start transient.  Backoff schedule: 2 s, 4 s, 8 s, …
        """
        result = await self._probe_once(identity, headers, auth_type)
        for attempt in range(self._startup_retries):
            if result.status != "target_unavailable":
                break
            wait = _BACKOFF_BASE * (2 ** attempt)  # 2 s, 4 s, 8 s
            logger.info(
                "bootstrap: target_unavailable (attempt %d/%d) — "
                "cold-start retry in %.0f s: %s",
                attempt + 1,
                self._startup_retries + 1,
                wait,
                result.error_detail,
            )
            await asyncio.sleep(wait)
            result = await self._probe_once(identity, headers, auth_type)
        return result

    async def _probe_once(
        self,
        identity: str,
        headers: dict[str, str],
        auth_type: str,
    ) -> CredentialCheckResult:
        """Send a single health-check probe to the endpoint and record the result.

        Args:
            identity: Human-readable name for the credential being checked.
            headers: Resolved auth headers (from AuthSession or AuthConfig).
            auth_type: Auth type string for reporting (bearer/basic/login_flow/…).
        """
        if self._is_websocket:
            return await self._probe_websocket_once(identity, headers, auth_type)
        request_headers = {
            "User-Agent": "nuguard-bootstrap/1.0",
            "Content-Type": "application/json",
            **headers,
        }
        # A natural greeting rather than a terse "ping" — a real chat app is far
        # more likely to produce a normal, non-empty JSON response to something
        # that reads like an actual first message, reducing false "healthy"
        # reads on apps that (legitimately or not) special-case single-word
        # pings with a short/empty reply. Still doesn't need to produce a
        # *meaningful* AI response; just needs a 2xx vs 4xx/5xx and a body we
        # can sanity-check below.
        # Extra static fields (chat_payload_extras) are merged in so apps that crash on
        # missing required fields (e.g. vehicleState) don't trip the target_unavailable check.
        probe_body = {**self._probe_payload_extras, "message": "Hello, how can you help me?"}
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
                # A 2xx with an empty/unparseable body is not necessarily broken —
                # some endpoints legitimately ack out-of-band (SSE/websocket
                # follow-up, fire-and-forget) — so this never downgrades status to
                # a failure. But it's also exactly the signature a wrong/missing
                # required payload field produces on apps that swallow validation
                # errors instead of returning 400/422 (e.g. this run's kscope
                # bug: 200 + empty body when `consumerID` is missing). Surfacing
                # it as a warning means a broken run shows up here, at bootstrap,
                # instead of silently "verifying" then burning the circuit
                # breaker on the same failure a few seconds later once real
                # scenarios start sending the identical payload shape.
                body_warning = ""
                if not resp.text.strip():
                    body_warning = (
                        "2xx response had an empty body — if scenario requests start "
                        "failing with JSON decode errors, this app may require an "
                        "additional field (check target.chat_payload_extras)"
                    )
                else:
                    try:
                        resp.json()
                    except ValueError:
                        body_warning = (
                            "2xx response body was not valid JSON — if scenario requests "
                            "start failing with JSON decode errors, this app may require "
                            "an additional field (check target.chat_payload_extras)"
                        )
                if body_warning:
                    logger.warning("bootstrap: identity=%s %s", identity, body_warning)
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=self.full_url,
                    status="ok",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                    response_text=resp.text[:500] if resp.text else "",
                    body_warning=body_warning,
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

            if resp.status_code in (404, 405):
                # The target is up and responding, but this specific route
                # either doesn't exist (404) or doesn't accept the HTTP method
                # we send (405) — a routing problem, not a payload-shape one.
                # Unlike 400/422 below, this is NOT "connectivity-ok": a chat
                # endpoint that 404s would previously be reported as verified,
                # then fail every real scenario request the same way.
                detail = (
                    "HTTP 404 — endpoint does not exist at this path"
                    if resp.status_code == 404
                    else "HTTP 405 — endpoint exists but does not accept this HTTP method"
                )
                logger.warning(
                    "bootstrap endpoint_not_found: identity=%s status=%d",
                    identity,
                    resp.status_code,
                )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=self.full_url,
                    status="endpoint_not_found",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                    error_detail=detail,
                )

            if 400 <= resp.status_code < 500:
                # Other 4xx (400, 422, …): the server responded, so it is
                # reachable. The probe payload likely doesn't match the API
                # contract — the actual scenario payloads will use the correct
                # format. Treat as connectivity-ok, but for 400/422 specifically
                # flag it: the endpoint is real, but the probe's minimal payload
                # didn't match its contract, so it may need chat_payload_extras
                # or a different payload key.
                logger.debug(
                    "bootstrap ok (probe format mismatch): identity=%s status=%d",
                    identity,
                    resp.status_code,
                )
                payload_hint = ""
                if resp.status_code in (400, 422):
                    payload_hint = (
                        f"HTTP {resp.status_code} — endpoint exists but rejected the probe "
                        "payload; may need target.chat_payload_extras or a different payload key"
                    )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=self.full_url,
                    status="ok",
                    http_status_code=resp.status_code,
                    response_time_ms=elapsed_ms,
                    payload_hint=payload_hint,
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

    async def _probe_websocket_once(
        self,
        identity: str,
        headers: dict[str, str],
        auth_type: str,
    ) -> CredentialCheckResult:
        """Confirm the target is reachable by opening (and closing) a WebSocket handshake.

        Header auth is forwarded via the Upgrade request headers (same
        ``AuthSession.headers()`` values as the HTTP path); first-message auth
        (``ws_auth_message``) is sent immediately after the handshake succeeds.
        A successful connect with no immediate rejection counts as "target
        reachable and auth accepted" — mirrors the HTTP probe's 2xx/format-mismatch
        handling since we don't need a meaningful chat response here.
        """
        import websockets  # noqa: PLC0415

        from nuguard.redteam.target.ws_client import to_ws_url  # noqa: PLC0415

        request_headers = {"User-Agent": "nuguard-bootstrap/1.0", **headers}
        ws_url = to_ws_url(self._target_url) + self._endpoint
        start = time.monotonic()
        try:
            async with websockets.connect(
                ws_url,
                additional_headers=request_headers,
                open_timeout=self._timeout,
            ) as ws:
                if self._ws_auth_message is not None:
                    import json  # noqa: PLC0415

                    await ws.send(json.dumps(self._ws_auth_message))
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.debug("bootstrap ok (websocket): identity=%s", identity)
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth_type,
                endpoint=ws_url,
                status="ok",
                response_time_ms=elapsed_ms,
            )
        except websockets.exceptions.InvalidStatus as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            status_code = exc.response.status_code
            if status_code in (401, 403):
                logger.warning(
                    "bootstrap auth_failed (websocket): identity=%s status=%d",
                    identity, status_code,
                )
                return CredentialCheckResult(
                    identity=identity,
                    auth_type=auth_type,
                    endpoint=ws_url,
                    status="auth_failed",
                    http_status_code=status_code,
                    response_time_ms=elapsed_ms,
                    error_detail=f"HTTP {status_code} on WebSocket handshake",
                )
            logger.warning(
                "bootstrap target_unavailable (websocket): identity=%s status=%d",
                identity, status_code,
            )
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth_type,
                endpoint=ws_url,
                status="target_unavailable",
                http_status_code=status_code,
                response_time_ms=elapsed_ms,
                error_detail=f"HTTP {status_code} on WebSocket handshake",
            )
        except (TimeoutError, OSError, websockets.exceptions.WebSocketException) as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            return CredentialCheckResult(
                identity=identity,
                auth_type=auth_type,
                endpoint=ws_url,
                status="target_unavailable",
                response_time_ms=elapsed_ms,
                error_detail=str(exc),
            )
