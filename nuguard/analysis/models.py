"""Pydantic models for nuguard analysis plugin results."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalysisResult(BaseModel):
    """Structured output returned by every AnalysisPlugin.

    status:
        "ok"      — plugin ran cleanly, no findings
        "warning" — plugin ran, produced heuristic/structural findings
        "failed"  — plugin ran and found confirmed vulnerabilities (CVE/HIGH+)
        "skipped" — plugin was not run (binary absent, config disabled, etc.)
        "error"   — plugin encountered an unexpected error
    """

    status: str
    plugin: str
    message: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
