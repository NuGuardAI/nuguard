"""Internal campaign dataclasses (public Pydantic exports come later)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ObjectiveStatus = Literal[
    "completed", "aborted", "deferred", "blocked", "effect_unknown", "failed_transport"
]


@dataclass
class ObjectiveExecutionRecord:
    """What one catalog objective did on one branch, kept per objective.

    Attribution stays on the objective even when several objectives share a
    conversation: ``turn_start``/``turn_end`` bound its turns in the branch
    session, and ``ancestor_setup_refs`` names the earlier objectives whose
    setup it relied on. An earlier disclosure can therefore never be claimed
    as this objective's new success.
    """

    catalog_id: str
    scenario_id: str
    branch_id: str
    turn_start: int
    turn_end: int = 0
    status: ObjectiveStatus = "completed"
    ancestor_setup_refs: tuple[str, ...] = ()
    step_results: list[Any] = field(default_factory=list)
    tool_traces: list[list[dict]] = field(default_factory=list)
    canary_hits: list[str] = field(default_factory=list)
    attack_accepted: bool = False          # a success signal fired (a claim, not proof)
    unknown_state: bool = False            # ambiguous transport failure on this branch
    resume_step_index: int = 0             # for deferred objectives
    defer_seconds: float = 0.0
    defer_reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def turns(self) -> int:
        return max(0, self.turn_end - self.turn_start)
