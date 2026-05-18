"""Pydantic data models shared across all nuguard capabilities."""
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport

__all__ = [
    "CredentialCheckResult",
    "TargetHealthReport",
]
