"""Typed result model for auth bootstrap and target connectivity checks."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class CredentialCheckResult(BaseModel):
    """Result of one credential verification attempt against the target."""

    identity: str                  # "default", or canary.json tenant_id
    auth_type: str                 # "bearer", "api_key", "basic", "none", "skipped"
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
        """Return checks that are neither ok nor skipped."""
        return [c for c in self.checks if c.status not in ("ok", "skipped")]

    def summary_lines(self) -> list[str]:
        """Human-readable one-line summary per credential for CLI output."""
        lines = []
        icons = {
            "ok": "✓",
            "auth_failed": "✗ AUTH",
            "target_unavailable": "✗ UNAVAILABLE",
            "skipped": "–",
        }
        for c in self.checks:
            icon = icons.get(c.status, "?")
            timing = f" ({c.response_time_ms:.0f}ms)" if c.response_time_ms is not None else ""
            detail = f" — {c.error_detail}" if c.error_detail else ""
            lines.append(f"  [{icon}] {c.identity} ({c.auth_type}){timing}{detail}")
        return lines
