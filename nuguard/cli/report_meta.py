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
    target_url: str = ""
    target_endpoint: str = ""

    @property
    def target_full_url(self) -> str:
        """Concatenated target URL + endpoint, or empty string if not set."""
        if not self.target_url:
            return ""
        base = self.target_url.rstrip("/")
        ep = self.target_endpoint or ""
        return f"{base}{ep}" if ep else base

    def to_dict(self) -> dict:
        d: dict = {
            "generated_at": self.timestamp,
            "llm": self.llm_models if self.llm_models else None,
            "verbose": self.verbose,
        }
        if self.target_full_url:
            d["target"] = self.target_full_url
        return d

    def to_markdown_lines(self) -> list[str]:
        llm_str = ", ".join(self.llm_models) if self.llm_models else "not used"
        lines = [
            f"**Generated:** {self.timestamp}  ",
            f"**LLM:** {llm_str}  ",
        ]
        if self.target_full_url:
            lines.append(f"**Target:** `{self.target_full_url}`  ")
        if self.verbose:
            lines.append("**Mode:** verbose  ")
        lines.append("")
        return lines

    def to_text_line(self) -> str:
        llm_str = ", ".join(self.llm_models) if self.llm_models else "not used"
        parts = [f"Generated: {self.timestamp}", f"LLM: {llm_str}"]
        if self.target_full_url:
            parts.append(f"Target: {self.target_full_url}")
        if self.verbose:
            parts.append("Mode: verbose")
        return "  |  ".join(parts)
