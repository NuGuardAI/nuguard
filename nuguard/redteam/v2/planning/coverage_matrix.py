"""Coverage matrix — the design's "coverage scorer" input.

Tracks, across three dimensions (SBOM nodes × policy clauses × technique
families), which were *generated* into objectives, *skipped* (with a reason), or
*blocked* (applicable but no target).  The report surfaces coverage gaps so the
final report can state not just what was tested, but what was not and why.

Counting invariant: each :class:`~nuguard.redteam.v2.planning.objective_generator.ScenarioObjective`
belongs to exactly one technique family, so
``sum(family.objective_count) == total_objectives``.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum


class CoverageStatus(str, Enum):
    GENERATED = "generated"  # produced at least one objective
    SKIPPED = "skipped"      # intentionally not tested (reason given)
    BLOCKED = "blocked"      # applicable but no target/binding available


@dataclass
class CoverageEntry:
    key: str
    status: CoverageStatus
    reason: str = ""
    objective_count: int = 0


@dataclass
class CoverageMatrix:
    """Coverage across the three planning dimensions."""

    sbom_nodes: dict[str, CoverageEntry] = field(default_factory=dict)
    policy_clauses: dict[str, CoverageEntry] = field(default_factory=dict)
    technique_families: dict[str, CoverageEntry] = field(default_factory=dict)
    total_objectives: int = 0

    # ── recording ────────────────────────────────────────────────────────────
    def record_objective(
        self,
        *,
        node_ids: Iterable[str],
        clauses: Iterable[str],
        family: str,
    ) -> None:
        """Register one generated objective against all dimensions it covers."""
        self.total_objectives += 1
        for nid in node_ids:
            self._bump(self.sbom_nodes, nid)
        for clause in clauses:
            self._bump(self.policy_clauses, clause)
        self._bump(self.technique_families, family)

    def mark_skipped(self, dimension: str, key: str, reason: str) -> None:
        self._mark(dimension, key, CoverageStatus.SKIPPED, reason)

    def mark_blocked(self, dimension: str, key: str, reason: str) -> None:
        self._mark(dimension, key, CoverageStatus.BLOCKED, reason)

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _bump(bucket: dict[str, CoverageEntry], key: str) -> None:
        entry = bucket.get(key)
        if entry is None:
            bucket[key] = CoverageEntry(key=key, status=CoverageStatus.GENERATED, objective_count=1)
        else:
            entry.status = CoverageStatus.GENERATED
            entry.objective_count += 1

    def _bucket(self, dimension: str) -> dict[str, CoverageEntry]:
        try:
            return {
                "sbom_node": self.sbom_nodes,
                "policy_clause": self.policy_clauses,
                "technique_family": self.technique_families,
            }[dimension]
        except KeyError as exc:  # pragma: no cover - programmer error
            raise ValueError(f"unknown coverage dimension {dimension!r}") from exc

    def _mark(self, dimension: str, key: str, status: CoverageStatus, reason: str) -> None:
        bucket = self._bucket(dimension)
        existing = bucket.get(key)
        # Never downgrade a GENERATED entry to skipped/blocked.
        if existing is not None and existing.status is CoverageStatus.GENERATED:
            return
        bucket[key] = CoverageEntry(key=key, status=status, reason=reason)

    # ── reporting ────────────────────────────────────────────────────────────
    def _dim_summary(self, bucket: dict[str, CoverageEntry]) -> dict[str, int]:
        out = {s.value: 0 for s in CoverageStatus}
        for entry in bucket.values():
            out[entry.status.value] += 1
        return out

    def gaps(self) -> list[CoverageEntry]:
        """All skipped/blocked entries across every dimension."""
        result: list[CoverageEntry] = []
        for bucket in (self.sbom_nodes, self.policy_clauses, self.technique_families):
            result.extend(
                e for e in bucket.values() if e.status is not CoverageStatus.GENERATED
            )
        return result

    def summary(self) -> dict[str, object]:
        return {
            "total_objectives": self.total_objectives,
            "sbom_nodes": self._dim_summary(self.sbom_nodes),
            "policy_clauses": self._dim_summary(self.policy_clauses),
            "technique_families": self._dim_summary(self.technique_families),
        }
