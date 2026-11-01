"""Coverage ledger: what was planned, attempted, completed, blocked or skipped.

Counts are kept separately (v5 spec §10) and a reused warm-up never counts as a
completed objective — only an objective's own record does.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Status(str, Enum):
    PLANNED = "planned"
    APPLICABLE = "applicable"
    ATTEMPTED = "attempted"
    COMPLETED = "meaningfully_completed"
    CONFIRMED = "confirmed"
    BLOCKED = "blocked"
    REDUNDANT = "redundant"
    DISABLED = "disabled"
    BUDGET_DEFERRED = "budget_deferred"


#: States that need no further scheduling.
TERMINAL = frozenset(
    {Status.COMPLETED, Status.CONFIRMED, Status.BLOCKED, Status.REDUNDANT, Status.DISABLED}
)

Dimension = Literal[
    "control", "catalog_id", "technique", "channel", "identity_boundary", "level", "owasp_version"
]


@dataclass
class LedgerEntry:
    """One objective's coverage record."""

    objective_id: str
    catalog_id: str
    control: str
    technique: str
    channel: str
    identity_boundary: str
    level: int
    owasp_versions: tuple[str, ...] = ("2026",)
    status: Status = Status.PLANNED
    reason: str = ""               # blocked / budget_deferred reason
    ref: str = ""                  # for redundant: the equivalent objective that was tested
    attempts: int = 0
    meaningful_attempts: int = 0

    def dimension_values(self, dim: Dimension) -> tuple[str, ...]:
        if dim == "owasp_version":
            return self.owasp_versions
        value = {
            "control": self.control,
            "catalog_id": self.catalog_id,
            "technique": self.technique,
            "channel": self.channel,
            "identity_boundary": self.identity_boundary,
            "level": f"L{self.level}",
        }[dim]
        return (value,)


@dataclass
class CoverageLedger:
    entries: dict[str, LedgerEntry] = field(default_factory=dict)

    def register(self, entry: LedgerEntry) -> None:
        self.entries[entry.objective_id] = entry

    def set_status(self, objective_id: str, status: Status, reason: str = "", ref: str = "") -> None:
        e = self.entries[objective_id]
        e.status, e.reason, e.ref = status, reason, ref

    def record_attempt(self, objective_id: str, *, meaningful: bool) -> None:
        """Record an attempt. Only a *meaningful* attempt completes the objective."""
        e = self.entries[objective_id]
        e.attempts += 1
        if meaningful:
            e.meaningful_attempts += 1
            if e.status not in (Status.CONFIRMED,):
                e.status = Status.COMPLETED
        elif e.status in (Status.PLANNED, Status.APPLICABLE, Status.BUDGET_DEFERRED):
            e.status = Status.ATTEMPTED

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in Status}
        for e in self.entries.values():
            out[e.status.value] += 1
        return out

    def by_dimension(self, dim: Dimension) -> dict[str, dict[str, int]]:
        table: dict[str, dict[str, int]] = defaultdict(lambda: {s.value: 0 for s in Status})
        for e in self.entries.values():
            for v in e.dimension_values(dim):
                table[v][e.status.value] += 1
        return dict(table)

    def unresolved(self) -> list[LedgerEntry]:
        """Applicable objectives that still need work (published when breadth won't fit)."""
        return [e for e in self.entries.values() if e.status not in TERMINAL]

    def covered_controls(self) -> set[str]:
        return {
            e.control for e in self.entries.values() if e.status in (Status.COMPLETED, Status.CONFIRMED)
        }
