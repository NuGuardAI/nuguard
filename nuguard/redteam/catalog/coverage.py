"""Coverage report: what the catalog generated and what it skipped."""
from __future__ import annotations

from dataclasses import dataclass, field

from .taxonomy import Capability, ScenarioCategory


@dataclass
class CoverageReport:
    """Result of a catalog selection pass."""

    profile: str                              # "ci" | "standard" | "full"
    total_generated: int = 0
    categories_covered: list[ScenarioCategory] = field(default_factory=list)
    per_category_count: dict[str, int] = field(default_factory=dict)
    # (catalog_id, category, reason)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)
    capabilities_detected: list[Capability] = field(default_factory=list)

    @property
    def categories_covered_count(self) -> int:
        return len(self.categories_covered)

    def to_markdown(self) -> str:
        lines: list[str] = [
            "## Catalog Coverage",
            "",
            f"- **Profile**: `{self.profile}`",
            f"- **Generated**: {self.total_generated} scenario instances",
            f"- **Categories covered**: {self.categories_covered_count} / 12",
            "",
        ]

        if self.per_category_count:
            lines += [
                "| Category | Instances |",
                "| --- | --- |",
            ]
            for cat, count in sorted(self.per_category_count.items()):
                lines.append(f"| {cat} | {count} |")
            lines.append("")

        if self.capabilities_detected:
            lines.append(
                "**Detected capabilities**: "
                + ", ".join(f"`{c.value}`" for c in sorted(self.capabilities_detected, key=lambda x: x.value))
            )
            lines.append("")

        if self.skipped:
            # Group by reason
            by_reason: dict[str, list[str]] = {}
            for sid, cat, reason in self.skipped:
                by_reason.setdefault(reason, []).append(sid)
            lines += [
                "**Skipped specs** (coverage gaps):",
                "",
            ]
            for reason, ids in sorted(by_reason.items()):
                lines.append(f"- *{reason}*: {', '.join(ids)}")
            lines.append("")

        return "\n".join(lines)
