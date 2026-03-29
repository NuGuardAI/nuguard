"""Shared report metadata — timestamp, LLM info, verbose flag."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ReportMeta:
    """Metadata attached to every NuGuard output report."""

    timestamp: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )
    llm_models: list[str] = field(default_factory=list)
    verbose: bool = False

    def to_dict(self) -> dict:
        return {
            "generated_at": self.timestamp,
            "llm": self.llm_models if self.llm_models else None,
            "verbose": self.verbose,
        }

    def to_markdown_lines(self) -> list[str]:
        llm_str = ", ".join(self.llm_models) if self.llm_models else "not used"
        lines = [
            f"**Generated:** {self.timestamp}  ",
            f"**LLM:** {llm_str}  ",
        ]
        if self.verbose:
            lines.append("**Mode:** verbose  ")
        lines.append("")
        return lines

    def to_text_line(self) -> str:
        llm_str = ", ".join(self.llm_models) if self.llm_models else "not used"
        parts = [f"Generated: {self.timestamp}", f"LLM: {llm_str}"]
        if self.verbose:
            parts.append("Mode: verbose")
        return "  |  ".join(parts)
