"""Score a redteam report against a challenge benchmark (e.g. the FinBot CTF).

A benchmark file maps each challenge to the workflow that attempts it; a challenge
is *solved* when the report contains a finding id ``wf-<workflow>-<hash>`` or the
``[workflow:<id>]`` marker that workflow findings carry in their description.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FINDING_RE = re.compile(r"wf-([a-z0-9][a-z0-9-]*?)-[0-9a-f]{8}\b|\[workflow:([a-z0-9-]+)\]")


@dataclass
class ChallengeResult:
    """Outcome for one benchmark challenge."""

    id: str
    difficulty: str
    status: str  # solved | attempted | gap | n/a
    workflow: str = ""
    note: str = ""


@dataclass
class BenchmarkScore:
    """Aggregate benchmark result."""

    results: list[ChallengeResult] = field(default_factory=list)

    def _count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def attemptable(self) -> int:
        return len(self.results) - self._count("n/a")

    @property
    def solved(self) -> int:
        return self._count("solved")

    @property
    def covered(self) -> int:
        """Challenges NuGuard has an attack for (solved or merely attempted)."""
        return self.solved + self._count("attempted")

    def to_markdown(self) -> str:
        lines = [
            "## CTF benchmark",
            "",
            f"- **Solved**: {self.solved}/{self.attemptable} "
            f"({round(100 * self.solved / self.attemptable) if self.attemptable else 0}%)",
            f"- **Covered by an attack**: {self.covered}/{self.attemptable}",
            "",
            "| Challenge | Difficulty | Status | Workflow / note |",
            "|---|---|---|---|",
        ]
        for r in self.results:
            lines.append(f"| {r.id} | {r.difficulty} | {r.status} | {r.workflow or r.note} |")
        return "\n".join(lines) + "\n"


def solved_workflows(report_text: str) -> set[str]:
    """Workflow ids that produced a finding in *report_text* (Markdown or JSON)."""
    return {a or b for a, b in _FINDING_RE.findall(report_text)}


def score(benchmark_path: Path, report_text: str) -> BenchmarkScore:
    """Score *report_text* against the benchmark file at *benchmark_path*."""
    data: dict[str, Any] = yaml.safe_load(benchmark_path.read_text(encoding="utf-8")) or {}
    hits = solved_workflows(report_text)
    out = BenchmarkScore()
    for ch in data.get("challenges", []):
        workflow = str(ch.get("workflow") or "")
        if ch.get("n/a"):
            status, note = "n/a", str(ch["n/a"])
        elif workflow:
            status, note = ("solved" if workflow in hits else "attempted"), str(ch.get("note") or "")
        else:
            status, note = "gap", str(ch.get("gap") or "")
        out.results.append(
            ChallengeResult(str(ch["id"]), str(ch.get("difficulty", "")), status, workflow, note)
        )
    return out
