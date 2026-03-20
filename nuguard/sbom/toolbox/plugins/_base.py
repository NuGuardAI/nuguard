"""Shared dataclasses for toolbox plugins."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """Result returned by every toolbox plugin.

    Attributes:
        status: ``"pass"`` | ``"fail"`` | ``"warn"``
        message: Human-readable summary.
        details: Optional list of per-finding dicts.
    """

    status: str  # "pass" | "fail" | "warn"
    message: str
    details: list[dict] = field(default_factory=list)
