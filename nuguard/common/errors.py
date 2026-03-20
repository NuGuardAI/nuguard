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
