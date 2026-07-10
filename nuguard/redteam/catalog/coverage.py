"""Coverage report: what the catalog generated and what it skipped."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .taxonomy import Capability, ScenarioCategory


def render_catalog_coverage_markdown(data: dict[str, Any]) -> str:
    """Render a catalog-coverage JSON snapshot as Markdown.

    Standalone counterpart to :meth:`CoverageReport.to_markdown` for callers
    that only have the JSON-safe dict form produced by
    :func:`nuguard.redteam.public_api._catalog_coverage_to_dict` (e.g.
    ``RedteamRunResult.catalog_coverage``, which cannot carry the live object).
    """
    profile = data.get("profile", "")
    total_generated = data.get("total_generated", 0)
    categories_covered = data.get("categories_covered") or []
    per_category_count = data.get("per_category_count") or {}
    capabilities_detected = data.get("capabilities_detected") or []
    skipped = data.get("skipped") or []

    lines: list[str] = [
        "## Catalog Coverage",
        "",
        f"- **Profile**: `{profile}`",
        f"- **Generated**: {total_generated} scenario instances",
        f"- **Categories covered**: {len(categories_covered)} / 12",
        "",
    ]

    if per_category_count:
        lines += [
            "| Category | Instances |",
            "| --- | --- |",
        ]
        for cat, count in sorted(per_category_count.items()):
            lines.append(f"| {cat} | {count} |")
        lines.append("")

    if capabilities_detected:
        lines.append(
            "**Detected capabilities**: "
            + ", ".join(f"`{c}`" for c in sorted(capabilities_detected))
        )
        lines.append("")

    if skipped:
        # Group by reason
        by_reason: dict[str, list[str]] = {}
        for sid, _cat, reason in skipped:
            by_reason.setdefault(reason, []).append(sid)
        lines += [
            "**Skipped specs** (coverage gaps):",
            "",
        ]
        for reason, ids in sorted(by_reason.items()):
            lines.append(f"- *{reason}*: {', '.join(ids)}")
        lines.append("")

    return "\n".join(lines)


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
        return render_catalog_coverage_markdown({
            "profile": self.profile,
            "total_generated": self.total_generated,
            "categories_covered": [c.value for c in self.categories_covered],
            "per_category_count": self.per_category_count,
            "capabilities_detected": [c.value for c in self.capabilities_detected],
            "skipped": self.skipped,
        })
