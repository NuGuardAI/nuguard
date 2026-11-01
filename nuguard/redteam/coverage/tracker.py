"""Redteam coverage tracker.

Records which SBOM nodes and policy clauses were covered during a redteam run,
including scenarios that were generated, executed, or skipped due to profile caps.
Produces a Markdown table suitable for embedding in the redteam report.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# Tool names that warrant a coverage gate: code/shell execution, egress, deletion,
# money movement, and storage/user management.
_HIGH_RISK_TOOL_RE = re.compile(
    r"exec|shell|network_request|delete|send_email|transfer|manage_storage|manage_users|"
    r"read_config|database_maintenance",
    re.IGNORECASE,
)
# Node types that must each be exercised at least once.
_GATED_TYPES = ("AGENT", "DATASTORE", "AUTH")


def is_gated_node(node_type: str, name: str) -> bool:
    """True when a node of *node_type* must be covered by at least one executed scenario."""
    if node_type in _GATED_TYPES:
        return True
    return node_type == "TOOL" and bool(_HIGH_RISK_TOOL_RE.search(name))


@dataclass
class CoverageEntry:
    node_id: str
    node_type: str
    name: str
    generated: int = 0
    executed: int = 0
    findings: int = 0
    skipped_reason: str = ""


def render_coverage_markdown(data: dict[str, Any]) -> str:
    """Render a coverage-tracker JSON snapshot as Markdown.

    Standalone counterpart to :meth:`CoverageTracker.to_markdown` for callers
    that only have the JSON-safe dict form produced by
    :meth:`CoverageTracker.to_dict` (e.g. ``RedteamRunResult.coverage_tracker``
    from :mod:`nuguard.redteam.public_api`, which cannot carry the live object).
    """
    nodes = data.get("nodes") or []
    policy_clauses = data.get("policy_clauses") or []
    capped_count = data.get("capped_count") or 0

    if not nodes and not policy_clauses and not data.get("uncovered"):
        return ""

    lines: list[str] = ["## SBOM Coverage", ""]
    lines.append("| Node | Type | Generated | Executed | Findings |")
    lines.append("|---|---|---|---|---|")

    all_entries = list(nodes) + list(policy_clauses)
    for entry in sorted(all_entries, key=lambda e: (-e["generated"], e["node_type"], e["name"])):
        name = entry["name"]
        name_display = name[:50] + ("…" if len(name) > 50 else "")
        lines.append(
            f"| {name_display} | {entry['node_type']} "
            f"| {entry['generated']} | {entry['executed']} | {entry['findings']} |"
        )

    lines.append("")

    gaps = data.get("uncovered") or []
    if gaps:
        lines.append(f"### Coverage gaps ({len(gaps)} gated node(s) never executed)")
        lines.append("")
        lines.append("| Node | Type | Reason |")
        lines.append("|---|---|---|")
        for gap in gaps:
            lines.append(f"| {gap['name'][:50]} | {gap['node_type']} | {gap['reason']} |")
        lines.append("")

    if capped_count:
        lines.append(
            f"_Profile note: {capped_count} additional scenario(s) were "
            "skipped due to per-goal agent caps. Use the `full` profile to include them._"
        )
        lines.append("")

    return "\n".join(lines)


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
        self._expected: dict[str, tuple[str, str]] = {}

    def set_expected(self, nodes: list[tuple[str, str, str]]) -> None:
        """Register ``(node_id, node_type, name)`` of every node the run should cover.

        Only gated nodes (see :func:`is_gated_node`) are kept; they surface in
        ``uncovered`` when no scenario executed against them.
        """
        for node_id, node_type, name in nodes:
            if is_gated_node(node_type, name):
                self._expected[node_id] = (node_type, name)

    @property
    def uncovered(self) -> list[dict[str, str]]:
        """Gated nodes with zero executed scenarios, each with the reason."""
        out: list[dict[str, str]] = []
        for node_id, (node_type, name) in self._expected.items():
            entry = self._nodes.get(node_id)
            if entry is not None and entry.executed > 0:
                continue
            reason = (
                "no scenario generated"
                if entry is None or entry.generated == 0
                else f"{entry.generated} generated, none executed"
            )
            out.append({"node_id": node_id, "node_type": node_type, "name": name, "reason": reason})
        return sorted(out, key=lambda g: (g["node_type"], g["name"]))

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
        return render_coverage_markdown(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe snapshot of accumulated coverage.

        Complements :meth:`to_markdown` (report rendering) with a structured
        form consumable by :mod:`nuguard.redteam.public_api`.
        """
        return {
            "nodes": [asdict(e) for e in self._nodes.values()],
            "policy_clauses": [asdict(e) for e in self._policy_clauses.values()],
            "capped_count": self._capped_count,
            "uncovered": self.uncovered,
        }
