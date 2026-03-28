"""Cognitive Policy and compliance data models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ComplianceResult(str, Enum):
    """Overall compliance status for a single control evaluation."""

    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    UNABLE_TO_ASSESS = "unable_to_assess"
    NOT_APPLICABLE = "not_applicable"


class ControlType(str, Enum):
    """Indicates how a control is assessed."""

    AUTOMATED = "automated"
    ATTESTATION_REQUIRED = "attestation_required"
    NOT_APPLICABLE = "not_applicable"


# ---------------------------------------------------------------------------
# Framework reference
# ---------------------------------------------------------------------------


class FrameworkRef(BaseModel):
    """Cross-reference to a specific control in an external framework."""

    framework: str = Field(description="Framework name, e.g. 'OWASP_LLM_TOP10'")
    control_id: str = Field(description="Control identifier within the framework")
    url: str | None = Field(
        default=None, description="Canonical URL for this control"
    )


# ---------------------------------------------------------------------------
# Compliance control definition
# ---------------------------------------------------------------------------


class ComplianceControl(BaseModel):
    """A single compliance control definition loaded from a framework bundle."""

    id: str = Field(description="Unique control identifier")
    name: str = Field(description="Short human-readable name")
    description: str = Field(description="Full description of the control")
    framework: str = Field(description="Owning framework name")
    framework_refs: list[FrameworkRef] = Field(
        default_factory=list,
        description="Cross-references to external frameworks",
    )
    control_type: ControlType = Field(
        default=ControlType.AUTOMATED,
        description="How this control is assessed",
    )
    ai_sbom_assessable: bool = Field(
        default=True,
        description="Whether this control can be evaluated from an AI-SBOM",
    )
    manual_attestation_required: bool = Field(
        default=False,
        description="Whether human attestation is required in addition to automation",
    )
    ai_sbom_basis: list[str] = Field(
        default_factory=list,
        description="Node types that provide evidence for this control",
    )
    severity: str = Field(
        default="medium",
        description="Risk severity: critical / high / medium / low",
    )
    gap_diagnosis: str = Field(
        default="",
        description="Human-readable explanation of what a gap looks like",
    )
    fix_guidance: str = Field(
        default="",
        description="Actionable remediation guidance",
    )
    tags: list[str] = Field(default_factory=list)
    ccd: dict[str, Any] | None = Field(
        default=None,
        description="Raw Compliance Control Descriptor (parsed by CCDParser)",
    )


# ---------------------------------------------------------------------------
# Control evaluation result
# ---------------------------------------------------------------------------


class ControlEvaluation(BaseModel):
    """Result of evaluating a single ComplianceControl against an AI-SBOM."""

    control: ComplianceControl
    result: ComplianceResult
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Numeric score in [0, 1]; 1.0 = fully compliant",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Human-readable evidence items supporting the result",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Specific gaps identified during evaluation",
    )
    remediation: str = Field(
        default="",
        description="Actionable remediation guidance derived from the evaluation",
    )


# ---------------------------------------------------------------------------
# Assessment-level rollup
# ---------------------------------------------------------------------------


class PolicyAssessmentResult(BaseModel):
    """Aggregated compliance assessment result for one framework."""

    framework: str = Field(description="Framework that was assessed")
    total_controls: int = Field(description="Total number of controls evaluated")
    pass_count: int = Field(default=0)
    partial_count: int = Field(default=0)
    fail_count: int = Field(default=0)
    unable_count: int = Field(default=0)
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Weighted compliance score across all controls",
    )
    evaluations: list[ControlEvaluation] = Field(
        default_factory=list,
        description="Per-control evaluation details",
    )


# ---------------------------------------------------------------------------
# Cognitive policy (parsed from Markdown)
# ---------------------------------------------------------------------------


class CognitivePolicy(BaseModel):
    """Parsed cognitive policy for an AI application."""

    allowed_topics: list[str] = Field(default_factory=list)
    restricted_topics: list[str] = Field(default_factory=list)
    restricted_actions: list[str] = Field(default_factory=list)
    hitl_triggers: list[str] = Field(default_factory=list)
    data_classification: list[str] = Field(default_factory=list)
    rate_limits: dict[str, int] = Field(default_factory=dict)
    raw_sections: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Unrecognised Markdown sections preserved verbatim",
    )


# ---------------------------------------------------------------------------
# Compiled policy controls (derived from CognitivePolicy for validate/redteam)
# ---------------------------------------------------------------------------


class PolicyControl(BaseModel):
    """A single testable control compiled from a CognitivePolicy.

    Each control represents one enforceable rule from the policy with
    associated prompts that validate and redteam modules can use directly to
    exercise the control.
    """

    id: str = Field(description="Unique control identifier, e.g. 'CTRL-001'")
    section: str = Field(
        description="Source policy section, e.g. 'restricted_actions', 'hitl_triggers'"
    )
    description: str = Field(description="The policy statement verbatim")
    control_type: str = Field(
        description=(
            "One of: topic_restriction, action_restriction, "
            "hitl, data_protection, rate_limit"
        )
    )
    severity: str = Field(default="medium", description="critical | high | medium | low")
    test_prompts: list[str] = Field(
        default_factory=list,
        description="User messages that exercise this control (should be allowed / handled correctly)",
    )
    boundary_prompts: list[str] = Field(
        default_factory=list,
        description="User messages that attempt to violate this control (should be refused/escalated)",
    )
