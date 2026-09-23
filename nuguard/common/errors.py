"""Shared exception hierarchy for NuGuard."""


class NuGuardError(Exception):
    """Base exception for all NuGuard errors."""


class SbomError(NuGuardError):
    """Raised when SBOM parsing or generation fails."""


class ValidationError(NuGuardError):
    """Raised when schema or semantic validation fails."""


class ScanError(NuGuardError):
    """Raised when a scan cannot proceed or fails mid-execution."""


class ConfigError(NuGuardError):
    """Raised when configuration is missing, malformed, or contradictory."""


class ExtractorError(NuGuardError):
    """Raised when the SBOM extractor encounters an unrecoverable error."""


class AuthError(NuGuardError):
    """Raised when authentication fails (401/403) during bootstrap or a live run.

    Attributes:
        status_code: HTTP status returned by the target (401, 403, or 0 for
            pre-request errors).
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


class BrowserLoginError(NuGuardError):
    """Raised when a Playwright-driven browser login fails.

    Attributes:
        step: Which stage failed — one of ``"playwright_not_installed"``,
            ``"browser_binary_missing"``, ``"navigate"``, ``"find_login_trigger"``,
            ``"fill_credentials"``, ``"missing_credentials"``, ``"await_redirect"``,
            ``"cookie_export"``, ``"yaml_write"``, ``"yaml_write_conflict"``, or
            ``"unknown"``. Callers map this to an exit code/user message instead
            of string-matching the exception text.
        url: The target URL being processed, if known.
        cause: Underlying exception, if any, stringified for diagnostics.
    """

    def __init__(
        self,
        message: str,
        step: str = "unknown",
        url: str = "",
        cause: str = "",
    ) -> None:
        super().__init__(message)
        self.step = step
        self.url = url
        self.cause = cause


class TargetUnavailableError(NuGuardError):
    """Raised when the target is unreachable (network error, 5xx, circuit breaker).

    Attributes:
        url: The URL that failed.
        cause: Underlying exception or HTTP status that triggered the error.
        source: Which failure path tripped the breaker — ``"chat"`` (the
            conversational ``send()``/``send_stream()`` endpoint) or
            ``"endpoint_probe"`` (a direct-HTTP ``invoke_endpoint()`` call
            against an SBOM-derived REST path). Callers that need to isolate
            direct-HTTP probe outages from genuine chat-endpoint outages
            (e.g. the redteam orchestrator's circuit breaker) inspect this
            instead of the message text. Defaults to ``"chat"`` for backward
            compatibility with existing call sites.
    """

    def __init__(
        self,
        message: str,
        url: str = "",
        cause: str = "",
        source: str = "chat",
    ) -> None:
        super().__init__(message)
        self.url = url
        self.cause = cause
        self.source = source


class TargetEndpointNotFoundError(NuGuardError):
    """Raised when the configured/resolved chat endpoint returns 404 or 405.

    Distinct from :class:`TargetUnavailableError`: the target itself is up and
    responding, but this specific route doesn't exist (404) or doesn't accept
    the HTTP method NuGuard sends (405). Retrying the same request won't help —
    the fix is a different endpoint, not a later attempt.

    Attributes:
        url: The endpoint URL that returned 404/405.
        http_status_code: 404 or 405.
        detail: Raw response detail for diagnostics.
    """

    def __init__(
        self,
        message: str,
        url: str = "",
        http_status_code: int = 0,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.url = url
        self.http_status_code = http_status_code
        self.detail = detail


class TargetRateLimitedError(NuGuardError):
    """Raised when the target returns HTTP 429 during endpoint discovery.

    A target-wide condition, not a per-candidate one — getting 429 on one
    candidate path means the remaining candidates are likely to hit the same
    quota, so discovery stops entirely rather than continuing to probe.

    Attributes:
        url: The URL that returned 429.
        retry_after: Seconds to wait before retrying, parsed from the
            response's ``Retry-After`` header, or ``None`` if absent/unparseable.
        detail: Raw response detail for diagnostics.
    """

    def __init__(
        self,
        message: str,
        url: str = "",
        retry_after: float | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.url = url
        self.retry_after = retry_after
        self.detail = detail
