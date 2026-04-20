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
    finding_triggers: dict[str, bool] = field(default_factory=dict)

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
        if self.finding_triggers:
            d["finding_triggers"] = self.finding_triggers
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
        if self.finding_triggers:
            trigger_parts = [f"{k}={'on' if v else 'off'}" for k, v in self.finding_triggers.items()]
            lines.append(f"**Finding Triggers:** {', '.join(trigger_parts)}  ")
        lines.append("")
        return lines

    def to_text_line(self) -> str:
        llm_str = ", ".join(self.llm_models) if self.llm_models else "not used"
        parts = [f"Generated: {self.timestamp}", f"LLM: {llm_str}"]
        if self.target_full_url:
            parts.append(f"Target: {self.target_full_url}")
        if self.verbose:
            parts.append("Mode: verbose")
        if self.finding_triggers:
            trigger_parts = [f"{k}={'on' if v else 'off'}" for k, v in self.finding_triggers.items()]
            parts.append(f"Triggers: {', '.join(trigger_parts)}")
        return "  |  ".join(parts)
