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
