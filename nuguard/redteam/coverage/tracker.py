"""Redteam coverage tracker.

Records which SBOM nodes and policy clauses were covered during a redteam run,
including scenarios that were generated, executed, or skipped due to profile caps.
Produces a Markdown table suitable for embedding in the redteam report.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CoverageEntry:
    node_id: str
    node_type: str
    name: str
    generated: int = 0
    executed: int = 0
    findings: int = 0
    skipped_reason: str = ""


class CoverageTracker:
    """Accumulates coverage data across scenario generation and execution.

    Usage::

        tracker = CoverageTracker()
        # During generation:
        tracker.record_generated(node_id, node_type, name)
        tracker.record_capped()  # when _MAX_AGENTS_PER_GOAL cap fires
        # During execution:
        tracker.record_executed(node_id)
        tracker.record_finding(node_id)
    """

    def __init__(self) -> None:
        self._nodes: dict[str, CoverageEntry] = {}
        self._policy_clauses: dict[str, CoverageEntry] = {}
        self._capped_count: int = 0

    def record_generated(self, node_id: str, node_type: str, name: str) -> None:
        entry = self._nodes.setdefault(
            node_id, CoverageEntry(node_id=node_id, node_type=node_type, name=name)
        )
        entry.generated += 1

    def record_policy_clause(self, clause: str) -> None:
        entry = self._policy_clauses.setdefault(
            clause, CoverageEntry(node_id=clause, node_type="policy", name=clause)
        )
        entry.generated += 1

    def record_executed(self, node_id: str) -> None:
        if node_id in self._nodes:
            self._nodes[node_id].executed += 1

    def record_finding(self, node_id: str) -> None:
        if node_id in self._nodes:
            self._nodes[node_id].findings += 1

    def record_capped(self) -> None:
        self._capped_count += 1

    @property
    def capped_count(self) -> int:
        return self._capped_count

    @property
    def total_generated(self) -> int:
        return sum(e.generated for e in self._nodes.values()) + sum(
            e.generated for e in self._policy_clauses.values()
        )

    @property
    def total_executed(self) -> int:
        return sum(e.executed for e in self._nodes.values())

    @property
    def total_findings(self) -> int:
        return sum(e.findings for e in self._nodes.values())

    def to_markdown(self) -> str:
        if not self._nodes and not self._policy_clauses:
            return ""

        lines: list[str] = ["## SBOM Coverage", ""]
        lines.append("| Node | Type | Generated | Executed | Findings |")
        lines.append("|---|---|---|---|---|")

        all_entries = list(self._nodes.values()) + list(self._policy_clauses.values())
        for entry in sorted(all_entries, key=lambda e: (-e.generated, e.node_type, e.name)):
            name_display = entry.name[:50] + ("…" if len(entry.name) > 50 else "")
            lines.append(
                f"| {name_display} | {entry.node_type} "
                f"| {entry.generated} | {entry.executed} | {entry.findings} |"
            )

        lines.append("")

        if self._capped_count:
            lines.append(
                f"_Profile note: {self._capped_count} additional scenario(s) were "
                "skipped due to per-goal agent caps. Use the `full` profile to include them._"
            )
            lines.append("")

        return "\n".join(lines)
