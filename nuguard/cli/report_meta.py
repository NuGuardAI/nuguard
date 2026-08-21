"""Shared report metadata — timestamp, run ID, LLM info, verbose flag."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ReportMeta:
    """Metadata attached to every NuGuard output report.

    ``run_id`` is the canonical correlation identifier for one CLI invocation:
    it is embedded in every machine-readable artifact (redteam/behavior JSON
    ``_meta``, remediation-plan JSON ``scan_id``) so outputs can be linked
    reliably. It is hidden from default Markdown/text reports and only shown
    when *verbose* is set.
    """

    timestamp: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    llm_models: list[str] = field(default_factory=list)
    verbose: bool = False
    target_url: str = ""
    target_endpoint: str = ""
    effective_endpoint: str = ""
    target_endpoint_source: str = "config"
    endpoint_discovery_notes: list[str] = field(default_factory=list)
    previous_run_profile: dict = field(default_factory=dict)
    finding_triggers: dict[str, bool] = field(default_factory=dict)
    scan_profile: str = ""

    @property
    def target_full_url(self) -> str:
        """Concatenated target URL + endpoint, or empty string if not set."""
        if not self.target_url:
            return ""
        base = self.target_url.rstrip("/")
        ep = self.effective_endpoint or self.target_endpoint or ""
        return f"{base}{ep}" if ep else base

    def to_dict(self) -> dict:
        d: dict = {
            "generated_at": self.timestamp,
            "run_id": self.run_id,
            "llm": self.llm_models if self.llm_models else None,
            "verbose": self.verbose,
        }
        if self.target_full_url:
            d["target"] = self.target_full_url
            d["effective_endpoint"] = self.effective_endpoint or self.target_endpoint or ""
            d["target_endpoint_source"] = self.target_endpoint_source
        if self.endpoint_discovery_notes:
            d["endpoint_discovery_notes"] = self.endpoint_discovery_notes
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
            endpoint = self.effective_endpoint or self.target_endpoint or ""
            if endpoint:
                lines.append(
                    f"**Effective Endpoint:** `{endpoint}` (source: {self.target_endpoint_source})  "
                )
        for note in self.endpoint_discovery_notes:
            lines.append(f"**Endpoint Note:** {note}  ")
        # Internal correlation IDs stay out of default user-facing reports.
        if self.verbose:
            lines.append(f"**Run ID:** `{self.run_id}`  ")
            lines.append("**Mode:** verbose  ")
        lines.append("")
        return lines

    def to_text_line(self) -> str:
        llm_str = ", ".join(self.llm_models) if self.llm_models else "not used"
        parts = [f"Generated: {self.timestamp}", f"LLM: {llm_str}"]
        if self.target_full_url:
            parts.append(f"Target: {self.target_full_url}")
            endpoint = self.effective_endpoint or self.target_endpoint or ""
            if endpoint:
                parts.append(f"Endpoint: {endpoint} ({self.target_endpoint_source})")
        if self.verbose:
            parts.append(f"Run ID: {self.run_id}")
            parts.append("Mode: verbose")
        if self.finding_triggers:
            trigger_parts = [f"{k}={'on' if v else 'off'}" for k, v in self.finding_triggers.items()]
            parts.append(f"Triggers: {', '.join(trigger_parts)}")
        return "  |  ".join(parts)
