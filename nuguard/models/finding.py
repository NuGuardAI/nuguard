"""Finding model for static analysis and redteam results."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    finding_id: str
    title: str
    severity: Severity
    description: str
    affected_component: str | None = None
    remediation: str | None = None
    references: list[str] = Field(default_factory=list)
    # Redteam-specific fields
    goal_type: str | None = None
    scenario_type: str | None = None
    sbom_path: list[str] = Field(default_factory=list)
    sbom_path_descriptions: list[str] = Field(default_factory=list)
    policy_clauses_violated: list[str] = Field(default_factory=list)
    chain_id: str | None = None
    owasp_asi_ref: str | None = None
    owasp_llm_ref: str | None = None
    mitre_atlas_technique: str | None = None
    evidence: str | None = None
    log_correlation_status: str | None = None
    # Redteam evidence fields — mirrors behavior/judge.py output for unified reporting
    reasoning: str = ""                          # one-sentence explanation of the finding
    evidence_quote: str = ""                     # exact substring of agent response proving the breach
    success_indicator: str | None = None         # named success indicator (e.g. "PII_disclosed")
    scores: dict[str, int | None] = Field(default_factory=dict)  # 1-5 rubric scores
    # Redteam step-level detail — each entry describes one executed attack step
    attack_steps: list[dict] = Field(default_factory=list)
    # Golden-data baseline — the authenticated test account's own data, captured via
    # a DISCOVER step and used to distinguish expected self-returns from genuine
    # cross-account leakage in IDOR/data-exfiltration scenarios. Populated only when
    # the scenario's chain used golden-data comparison.
    golden_ids: list[str] = Field(default_factory=list)
    golden_name: str | None = None
    golden_data_excerpt: str | None = None
    # Post-finding verification probe result (None = not run, True = reproduced, False = unconfirmed)
    verified: bool | None = None
    # NGRS (NuGuard Risk Score) — see nuguard.redteam.risk_engine.ngrs. 0-100
    # Impact x Likelihood score that `severity` above is banded from; `ngrs_vector`
    # is the human-readable factor breakdown (e.g. "DC:4/VOL:2/SC:1/ACT:3/EV:3/PRE:2/T:2/PM:+1")
    # so "why is this CRITICAL" is auditable from the finding alone.
    ngrs_score: int | None = None
    ngrs_vector: str | None = None
    # Progressive-methodology evidence fields (docs/claude-redteam-3.md) — "allow"
    # when the attack got through (a Finding was raised), "deny" when blocked;
    # guardrail_control mirrors LLMResponseEvaluator's closed-taxonomy refusal_reason
    # (content_filter, hitl_check, topic_guardrail, ...), i.e. "which control triggered".
    authorization_decision: str = ""
    guardrail_control: str = ""
