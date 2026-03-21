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
    sbom_path: list[str] = Field(default_factory=list)
    sbom_path_descriptions: list[str] = Field(default_factory=list)
    policy_clauses_violated: list[str] = Field(default_factory=list)
    chain_id: str | None = None
    owasp_asi_ref: str | None = None
    owasp_llm_ref: str | None = None
    mitre_atlas_technique: str | None = None
    evidence: str | None = None
    log_correlation_status: str | None = None
    # Redteam step-level detail — each entry describes one executed attack step
    attack_steps: list[dict] = Field(default_factory=list)
