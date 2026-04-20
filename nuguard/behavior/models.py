"""All Pydantic models and enums for the behavior module.

Models are aligned to the 4-layer scenario generation design and intent-aware
analysis.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BehaviorScenarioType(str, Enum):
    """Scenario types for the 4-layer test generation pipeline."""

    INTENT_HAPPY_PATH = "intent_happy_path"        # Layer 1
    COMPONENT_COVERAGE = "component_coverage"      # Layer 2
    BOUNDARY_ENFORCEMENT = "boundary_enforcement"  # Layer 3
    INVARIANT_PROBE = "invariant_probe"            # Layer 4
    DATA_DISCOVERY_PROBE = "data_discovery_probe"  # Layer 5: discover + react to user data


class BehaviorFindingType(str, Enum):
    """The type of finding detected during behavior analysis."""

    CAPABILITY_GAP = "CAPABILITY_GAP"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    BOUNDARY_FAILURE = "BOUNDARY_FAILURE"
    SECRET_DISCLOSURE = "SECRET_DISCLOSURE"
    INTENT_MISALIGNMENT = "INTENT_MISALIGNMENT"
    DATA_HANDLING_VIOLATION = "DATA_HANDLING_VIOLATION"
    ESCALATION_BYPASS = "ESCALATION_BYPASS"


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------


class IntentProfile(BaseModel):
    """Semantic interpretation of the Cognitive Policy.

    Captures the designer's intent for the AI application — what it should do,
    what it must not do, how data must be handled, and when to escalate.
    """

    app_purpose: str = ""
    core_capabilities: list[str] = Field(default_factory=list)
    behavioral_bounds: list[str] = Field(default_factory=list)
    data_handling_rules: list[str] = Field(default_factory=list)
    escalation_rules: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class BehaviorScenario(BaseModel):
    """A single runnable behavior test scenario."""

    scenario_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scenario_type: BehaviorScenarioType
    name: str
    messages: list[str] = Field(default_factory=list)
    expect_refused: bool = False
    forbid_pattern: str = ""
    policy_clauses: list[str] = Field(default_factory=list)
    target_component: str = ""
    target_component_type: str = ""
    target_endpoint: str | None = None
    goal: str = ""
    component_description: str = ""


# ---------------------------------------------------------------------------
# Per-turn records
# ---------------------------------------------------------------------------


class TurnRecord(BaseModel):
    """Per-turn record capturing prompt, response, and judgment."""

    turn: int
    prompt: str
    response: str
    tool_calls: list[dict] = Field(default_factory=list)
    violations: list[dict] = Field(default_factory=list)
    canary_hits: list[str] = Field(default_factory=list)
    passed: bool = True
    scenario_name: str = ""
    scenario_type: str = ""
    verdict: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    overall_score: float = 0.0
    gaps: list[str] = Field(default_factory=list)
    agents_mentioned: list[str] = Field(default_factory=list)
    tools_mentioned: list[str] = Field(default_factory=list)
    is_coverage_turn: bool = False
    latency_ms: int = 0
    deviations: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


class BehaviorDeviation(BaseModel):
    """A single observed deviation from intended behavior."""

    deviation_type: str
    description: str
    expected_behavior: str
    actual_behavior: str
    turn_number: int
    severity: str
    evidence: str


class BehaviorCoverage(BaseModel):
    """Per-component behavioral coverage."""

    component_name: str
    node_type: str
    exercised: bool = False
    exercised_within_policy: bool = False
    exercised_against_policy: bool = False
    deviations: list[BehaviorDeviation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class ScenarioResult(BaseModel):
    """Per-scenario aggregate result."""

    scenario_id: str
    scenario_name: str
    scenario_type: str
    verdicts: list[dict] = Field(default_factory=list)
    overall_score: float = 0.0
    confidence: float = 1.0
    coverage_pct: float = 0.0
    uncovered_agents: list[str] = Field(default_factory=list)
    uncovered_tools: list[str] = Field(default_factory=list)
    total_turns: int = 0
    coverage_turns: int = 0
    deviations: list[dict] = Field(default_factory=list)


class Recommendation(BaseModel):
    """Actionable remediation recommendation."""

    component: str
    recommendation_type: str
    description: str
    rationale: str
    priority: str
    related_findings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Remediation artefacts (concrete, node-specific remediations)
# ---------------------------------------------------------------------------


class RemediationArtefactType(str, Enum):
    """The type of concrete remediation artefact produced by RemediationSynthesizer."""

    SYSTEM_PROMPT_PATCH = "system_prompt_patch"
    INPUT_GUARDRAIL = "input_guardrail"
    OUTPUT_GUARDRAIL = "output_guardrail"
    ARCHITECTURAL_CHANGE = "architectural_change"


class RemediationArtefact(BaseModel):
    """A concrete, SBOM-node-specific remediation action.

    Produced by RemediationSynthesizer as the final pass after all findings
    are collected. Each artefact targets a specific SBOM node and provides
    actionable instructions (patch text, guardrail spec, or architectural change).
    """

    finding_ids: list[str] = Field(default_factory=list)
    """Finding IDs this artefact addresses."""

    component: str
    """Affected SBOM node name."""

    component_type: str
    """Node type: AGENT | TOOL | GUARDRAIL | system."""

    artefact_type: RemediationArtefactType
    priority: str  # critical | high | medium | low

    # System prompt patch fields
    patch_location: str | None = None
    """Source file location of the prompt, e.g. 'webapp/prompts/system.py:12'."""
    patch_section: str | None = None
    """Section heading to add/replace in the system prompt."""
    patch_text: str | None = None
    """Exact text to insert into the system prompt."""

    # Guardrail fields
    guardrail_name: str | None = None
    guardrail_type: str | None = None
    """input_classifier | output_redactor | regex | topic_classifier |
    auth_check | allowlist | confirmation_required | rate_limiter"""
    guardrail_trigger: str | None = None
    """Condition that activates the guardrail (regex, topic label, auth check, etc.)."""
    guardrail_action: str | None = None
    """BLOCK | REDACT | ROUTE | ESCALATE | HOLD"""
    guardrail_message: str | None = None
    """User-facing message shown when the guardrail fires."""

    # Privilege-specific fields (BA-005, BA-003, BA-006)
    privilege_scope: str | None = None
    """PrivilegeScope value: db_write | admin | filesystem_write | code_execution | ..."""
    privilege_node: str | None = None
    """Name of the PRIVILEGE SBOM node involved."""
    requires_auth: bool = False
    """True when an AUTH node must be added to protect this component."""
    requires_hitl: bool = False
    """True when HITL approval is mandated before the privileged action."""
    edge_to_remove: tuple[str, str] | None = None
    """(source_node, target_node) CALLS edge to remove if access is unnecessary."""

    # Architectural change fields
    change_description: str | None = None
    change_detail: str | None = None

    rationale: str
    """Human-readable explanation of why this remediation is needed."""


class BehaviorRunResult(BaseModel):
    """Complete result of the dynamic behavior analysis."""

    run_id: str
    findings: list[dict] = Field(default_factory=list)
    turn_records: list[TurnRecord] = Field(default_factory=list)
    scenario_results: list[ScenarioResult] = Field(default_factory=list)
    scenarios_executed: int = 0
    scan_outcome: str = "no_findings"
    coverage: list[BehaviorCoverage] = Field(default_factory=list)


class BehaviorAnalysisResult(BaseModel):
    """Complete output of the full behavior analysis pipeline (static + dynamic)."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    intent: IntentProfile
    static_findings: list[dict] = Field(default_factory=list)
    dynamic_findings: list[dict] = Field(default_factory=list)
    coverage: list[BehaviorCoverage] = Field(default_factory=list)
    scenario_results: list[ScenarioResult] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    remediation_plan: list[RemediationArtefact] = Field(default_factory=list)
    scan_outcome: str = "no_findings"

    @computed_field  # type: ignore[misc]
    @property
    def overall_risk_score(self) -> float:
        """Compute overall risk score from findings.

        Weights: critical=10, high=7, medium=4, low=1.
        Capped at 10.0.
        """
        all_findings: list[dict[str, Any]] = [
            *self.static_findings,
            *self.dynamic_findings,
        ]
        if not all_findings:
            return 0.0
        weights = {"critical": 10.0, "high": 7.0, "medium": 4.0, "low": 1.0, "info": 0.0}
        score = sum(weights.get(str(f.get("severity", "low")).lower(), 1.0) for f in all_findings)
        return round(min(score, 10.0), 2)

    @computed_field  # type: ignore[misc]
    @property
    def coverage_percentage(self) -> float:
        """Fraction of components that were exercised."""
        if not self.coverage:
            return 0.0
        exercised = sum(1 for c in self.coverage if c.exercised)
        return round(exercised / len(self.coverage), 4)

    @computed_field  # type: ignore[misc]
    @property
    def intent_alignment_score(self) -> float:
        """Average intent_alignment score across all scenario results, on a 1-5 scale."""
        scores: list[float] = []
        for sr in self.scenario_results:
            for v in sr.verdicts:
                s = v.get("scores", {}).get("intent_alignment")
                if s is not None:
                    scores.append(float(s))
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 2)
