"""Typed per-tool outcome of a static-analysis pass."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ToolRunStatus(str, Enum):
    """Closed set of outcomes for one analysis tool.

    ``ok``: the tool ran and finished. ``failed``: it was attempted and errored
    or could not reach its data source. ``skipped``: a precondition was missing
    (binary not installed, offline mode). ``disabled``: the request turned it
    off. ``not_applicable``: nothing for the tool to look at. ``timeout``: it
    hit its deadline.
    """

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    DISABLED = "disabled"
    NOT_APPLICABLE = "not_applicable"
    TIMEOUT = "timeout"


class ToolRun(BaseModel):
    """What one analysis tool actually did during a run."""

    tool: str = Field(description="nga-rules, osv, grype, checkov, trivy, semgrep, atlas, supply-chain")
    version: str | None = Field(default=None, description="Binary or rule-pack version used")
    status: ToolRunStatus
    findings: int = Field(default=0, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    reason: str | None = Field(
        default=None, description="Short sanitized cause; set unless status is ok or disabled"
    )

    @model_validator(mode="after")
    def _reason_required(self) -> "ToolRun":
        if self.status not in (ToolRunStatus.OK, ToolRunStatus.DISABLED) and not self.reason:
            raise ValueError(f"reason is required when status is {self.status.value}")
        return self
