"""Internal campaign dataclasses (public Pydantic exports come later)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ObjectiveStatus = Literal[
    "completed", "aborted", "deferred", "blocked", "effect_unknown", "failed_transport"
]


@dataclass
class ObjectiveRun:
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


# ── Public (JSON-safe) campaign result models ───────────────────────────────
# Runtime clients, sessions, headers/cookies and raw target transcripts are never
# part of these models; they carry counts, ids and statuses only.

from pydantic import BaseModel, Field  # noqa: E402


class CapabilityObservation(BaseModel):
    """A capability/knowledge item with its provenance and trust level."""

    kind: str
    key: str
    trust: Literal["declared", "claimed", "observed", "verified"]
    channel: Literal["baseline", "adversarial"]
    principal_ref: str = ""
    confidence: float = 0.0
    source_refs: list[str] = Field(default_factory=list)
    objective_ref: str = ""


class ConversationBranchSummary(BaseModel):
    """One conversation branch's lifecycle (no transcript, headers or cookies)."""

    branch_id: str
    parent_id: str | None = None
    generation: int = 0
    principal_ref: str = ""
    state: str = ""
    turns: int = 0
    baseline_done: bool = False
    contamination: list[str] = Field(default_factory=list)
    retire_reason: str | None = None
    objectives: list[str] = Field(default_factory=list)


class ObjectiveExecutionRecord(BaseModel):
    """What one catalog objective did, with its own turn range and setup attribution."""

    catalog_id: str
    scenario_id: str
    branch_id: str
    status: str
    turn_start: int = 0
    turn_end: int = 0
    ancestor_setup_refs: list[str] = Field(default_factory=list)
    attack_accepted: bool = False
    tool_trace_count: int = 0
    defer_reason: str = ""


class ReproductionRecord(BaseModel):
    """Fresh-session reproduction outcome for one candidate finding."""

    objective_id: str
    status: Literal["confirmed", "not_reproduced", "blocked", "not_attempted"]
    reason: str = ""
    minimal_turn_count: int = 0
    setup_replayed: list[str] = Field(default_factory=list)
    setup_skipped_writes: list[str] = Field(default_factory=list)
    trials: int = 0
    judge_calls: int = 0
    context_dependent: bool = False
    evidence_kind: str = ""
    effect_verified: bool = False


class CampaignPlanSummary(BaseModel):
    """Which objectives were grouped into campaigns and what fit the budget."""

    campaigns: list[dict[str, Any]] = Field(default_factory=list)
    breadth_selected: list[str] = Field(default_factory=list)
    breadth_deferred: list[str] = Field(default_factory=list)
    fits_budget: bool = True


CampaignPlan = CampaignPlanSummary


class CoverageSummary(BaseModel):
    """Coverage quality, kept separate from findings (spec §10).

    ``counts`` keeps planned / applicable / attempted / meaningfully_completed /
    confirmed / blocked / redundant / disabled / budget_deferred separate. A reused
    warm-up never counts as a completed objective.
    """

    counts: dict[str, int] = Field(default_factory=dict)
    by_control: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_catalog_id: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_technique: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_channel: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_identity_boundary: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_level: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_owasp_version: dict[str, dict[str, int]] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    blocked_reasons: dict[str, int] = Field(default_factory=dict)
    framework_versions: list[str] = Field(default_factory=list)
    inconclusive: bool = False
    """True when there are zero findings but untested/blocked/deferred controls remain."""


class EfficiencySummary(BaseModel):
    """What the run cost per unit of coverage."""

    target_requests: int = 0
    llm_calls: int = 0
    llm_est_tokens: int = 0
    llm_by_purpose: dict[str, int] = Field(default_factory=dict)
    branches_created: int = 0
    baselines_run: int = 0
    objectives_on_reused_branch: int = 0
    avoided_setup_requests_est: int = 0
    retry_delay_seconds: float = 0.0
    time_to_first_finding_s: float | None = None
    time_to_representative_coverage_s: float | None = None
    meaningful_objectives_per_100_requests: float = 0.0
