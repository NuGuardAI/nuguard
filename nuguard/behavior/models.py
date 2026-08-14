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

from nuguard.models.token_usage import TokenUsage
from nuguard.remediation.models import RemediationArtefact

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BehaviorScenarioType(str, Enum):
    """Scenario types for the behavior test generation pipeline."""

    INTENT_HAPPY_PATH = "intent_happy_path"        # Layer 1: topic-path scenarios
    AGENT_COVERAGE = "agent_coverage"              # Layer 2a: one scenario per AGENT node
    COMPONENT_COVERAGE = "component_coverage"      # Layer 2b: tool coverage (with chaining)
    GUARDRAIL_PROBE = "guardrail_probe"            # Layer 3: HITL + data classification
    DATA_DISCOVERY_PROBE = "data_discovery_probe"  # Layer 4: discover + react to user data
    ENDPOINT_COVERAGE = "endpoint_coverage"        # Layer 5: first-class API_ENDPOINT coverage
    GUIDED_COVERAGE = "guided_coverage"            # Layer 2c: live LLM-steered tool/agent coverage


class BehaviorFindingType(str, Enum):
    """The type of finding detected during behavior analysis."""

    CAPABILITY_GAP = "CAPABILITY_GAP"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    TOOL_CHAIN_BROKEN = "TOOL_CHAIN_BROKEN"
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
    # v5: tool/agent names that this scenario is responsible for exercising.
    # Coverage turns are scoped to these names only, preventing cross-domain
    # contamination where the coverage generator injects off-topic probes.
    scoped_tools: list[str] = Field(default_factory=list)
    scoped_agents: list[str] = Field(default_factory=list)
    # v7: SBOM-driven metadata
    matched_topic: str | None = None
    """The allowed_topic from cognitive policy that drove this scenario."""
    chain_source: list[str] = Field(default_factory=list)
    """Names of scenarios merged into this one via turn-chaining dedup."""
    tool_action_tiers: list[str] = Field(default_factory=list)
    """Action tier sequence (INFO/DECISION/ACTION) for chained scenarios."""
    primary_agent: str | None = None
    """Agent node ID owning this scenario (used for chaining grouping)."""
    scoped_guardrail: str | None = None
    """Guardrail node name this scenario validates (guardrail-path scenarios)."""
    warmup_turns: int = 0
    """Number of leading scripted turns that are non-substantive warm-up —
    excluded from deviation/finding scoring but still shown in the report."""


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
    effective_endpoint: str = ""
    request_url: str = ""


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
    evidence_mentions: list[str] = Field(default_factory=list)
    aliases_seen: list[str] = Field(default_factory=list)
    unmatched_mentions: list[str] = Field(default_factory=list)
    mapping_confidence: str | None = None
    mapped_from_endpoint: str | None = None


class BehaviorCounts(BaseModel):
    """Reconciled counts for findings and evidence rows."""

    static_findings: int = 0
    dynamic_findings: int = 0
    gap_findings: int = 0
    policy_dynamic_findings: int = 0
    total_unique_findings: int = 0
    deviation_evidence_items: int = 0
    raw_gap_observations: int = 0
    unique_gap_observations: int = 0


class BehaviorCoverageObjective(BaseModel):
    """A single SBOM surface (node or edge) mapped to a coverage objective.

    Every node and edge in the SBOM appears as a BehaviorCoverageObjective
    so reports can show which surfaces were dynamically exercised, statically
    checked, metadata-only, or explicitly not behavior-exercisable.
    """

    objective_id: str
    surface_type: str
    """'node' | 'edge' | 'field' | 'policy_clause'"""
    node_id: str | None = None
    node_name: str | None = None
    node_type: str | None = None
    edge_source: str | None = None
    edge_target: str | None = None
    relationship_type: str | None = None
    behavior_mode: str = "dynamic"
    """'dynamic' | 'static' | 'metadata_only' | 'not_behavior_exercisable'"""
    scenario_type: str | None = None
    status: str = "generated"
    """'generated' | 'executed' | 'passed' | 'failed' | 'skipped' | 'not_applicable'"""
    reason: str = ""


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
    matched_topic: str | None = None


class Recommendation(BaseModel):
    """Actionable remediation recommendation."""

    component: str
    recommendation_type: str
    description: str
    rationale: str
    priority: str
    related_findings: list[str] = Field(default_factory=list)


class BehaviorRunResult(BaseModel):
    """Complete result of the dynamic behavior analysis."""

    run_id: str
    findings: list[dict] = Field(default_factory=list)
    turn_records: list[TurnRecord] = Field(default_factory=list)
    scenario_results: list[ScenarioResult] = Field(default_factory=list)
    scenarios_executed: int = 0
    scan_outcome: str = "no_findings"
    coverage: list[BehaviorCoverage] = Field(default_factory=list)
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    config_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Run-level configuration notices surfaced to the report, e.g. automatic "
            "URL resolution when the configured target was a static hosting site."
        ),
    )
    gap_aggregation_stats: dict[str, int] = Field(default_factory=dict)
    coverage_mapping_diagnostics: dict[str, Any] = Field(default_factory=dict)
    effective_endpoint: str = ""
    target_endpoint_source: str = "config"
    remediation_plan: list[RemediationArtefact] = Field(default_factory=list)
    """Concrete, SBOM-node-specific remediation artefacts for ``findings``.

    Populated by :func:`nuguard.behavior.public_api.run_behavior_scenarios`
    (best-effort) via ``RemediationSynthesizer``. Empty when called directly
    through :class:`~nuguard.behavior.runner.BehaviorRunner`, which does not
    synthesize remediation itself.
    """


class BehaviorAnalysisResult(BaseModel):
    """Complete output of the full behavior analysis pipeline (static + dynamic)."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    intent: IntentProfile
    static_findings: list[dict] = Field(default_factory=list)
    dynamic_findings: list[dict] = Field(default_factory=list)
    coverage: list[BehaviorCoverage] = Field(default_factory=list)
    coverage_objectives: list[BehaviorCoverageObjective] = Field(default_factory=list)
    scenario_results: list[ScenarioResult] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    remediation_plan: list[RemediationArtefact] = Field(default_factory=list)
    scan_outcome: str = "no_findings"
    llm_executive_summary: str | None = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    config_notes: list[str] = Field(default_factory=list)
    gap_aggregation_stats: dict[str, int] = Field(default_factory=dict)
    coverage_mapping_diagnostics: dict[str, Any] = Field(default_factory=dict)
    effective_endpoint: str = ""
    target_endpoint_source: str = "config"
    run_profile: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[misc]
    @property
    def overall_risk_score(self) -> float:
        """Compute overall risk score (0–100) as the weighted average severity.

        Weights: critical=100, high=80, medium=50, low=25, info=0.
        Score = mean of per-finding weights, so volume and mix of severities
        influence the score proportionally. Consistent with red-team scoring.
        """
        all_findings: list[dict[str, Any]] = [
            *self.static_findings,
            *self.dynamic_findings,
        ]
        if not all_findings:
            return 0.0
        _weights = {"critical": 100.0, "high": 80.0, "medium": 50.0, "low": 25.0, "info": 0.0}

        def _sev_key(raw: object) -> str:
            # Normalise plain strings ("high", "HIGH") and enum-style strings
            # ("Severity.HIGH") produced by str(Severity.HIGH) on Python 3.12+.
            s = str(raw).lower()
            return s.rsplit(".", 1)[-1] if "." in s else s

        total = sum(_weights.get(_sev_key(f.get("severity", "low")), 25.0) for f in all_findings)
        return round(total / len(all_findings), 2)

    broken_chains: list[str] = Field(default_factory=list)
    """Names of chained scenarios where the chain broke mid-turn."""

    scenarios_skipped: list[str] = Field(default_factory=list)
    """Names of scenarios not executed because max_scenarios cap was reached."""

    allowed_topics_tested: list[str] = Field(default_factory=list)
    """Allowed topics from cognitive policy that were exercised."""

    dynamic_scan_outcome: str | None = None
    """Outcome from the dynamic runner phase alone (before static findings override it).
    Set to ``aborted_target_unavailable`` or ``inconclusive_target_errors`` when the
    dynamic phase encountered target errors, even if static findings changed the final
    ``scan_outcome``.
    """

    @computed_field  # type: ignore[misc]
    @property
    def coverage_percentage(self) -> float:
        """Fraction of agent/tool components that were exercised (backward-compatible)."""
        agent_tool = [c for c in self.coverage if c.node_type in ("AGENT", "TOOL")]
        if not agent_tool:
            return 0.0
        exercised = sum(1 for c in agent_tool if c.exercised)
        return round(exercised / len(agent_tool), 4)

    @computed_field  # type: ignore[misc]
    @property
    def endpoint_coverage_pct(self) -> float:
        """Fraction of API_ENDPOINT nodes that were exercised."""
        endpoints = [c for c in self.coverage if c.node_type == "API_ENDPOINT"]
        if not endpoints:
            return 0.0
        return round(sum(1 for c in endpoints if c.exercised) / len(endpoints), 4)

    @computed_field  # type: ignore[misc]
    @property
    def guardrail_coverage_pct(self) -> float:
        """Fraction of GUARDRAIL nodes that were exercised."""
        guardrails = [c for c in self.coverage if c.node_type == "GUARDRAIL"]
        if not guardrails:
            return 0.0
        return round(sum(1 for c in guardrails if c.exercised) / len(guardrails), 4)

    @computed_field  # type: ignore[misc]
    @property
    def intent_alignment_score(self) -> float:
        """Average topic_alignment score across all scenario results, on a 1-5 scale."""
        scores: list[float] = []
        for sr in self.scenario_results:
            for v in sr.verdicts:
                raw_scores = v.get("scores", {})
                # v7: topic_alignment replaces intent_alignment
                s = raw_scores.get("topic_alignment") or raw_scores.get("intent_alignment")
                if s is not None:
                    scores.append(float(s))
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 2)

    @computed_field  # type: ignore[misc]
    @property
    def topic_coverage_pct(self) -> float:
        """Fraction of allowed_topics that were exercised."""
        total = len(self.allowed_topics_tested)
        if total == 0:
            return 0.0
        covered = sum(
            1 for sr in self.scenario_results
            for _s in [sr]
            if _s.overall_score >= 2.0
        )
        return round(min(covered / total, 1.0), 4)

    @computed_field  # type: ignore[misc]
    @property
    def counts(self) -> BehaviorCounts:
        """Return reconciled finding/evidence counts for report summaries."""
        gap_findings = [
            f for f in self.dynamic_findings
            if str(f.get("finding_type", "")) in {
                BehaviorFindingType.CAPABILITY_GAP.value,
                BehaviorFindingType.INTENT_MISALIGNMENT.value,
                BehaviorFindingType.TOOL_CHAIN_BROKEN.value,
            }
        ]
        policy_dynamic = [f for f in self.dynamic_findings if f not in gap_findings]
        raw_gap = int(self.gap_aggregation_stats.get("raw_gap_observations", 0))
        uniq_gap = int(self.gap_aggregation_stats.get("unique_gap_observations", 0))
        deviation_rows = sum(
            1
            for sr in self.scenario_results
            for v in sr.verdicts
            for _ in (v.get("deviations") or [])
        )
        return BehaviorCounts(
            static_findings=len(self.static_findings),
            dynamic_findings=len(self.dynamic_findings),
            gap_findings=len(gap_findings),
            policy_dynamic_findings=len(policy_dynamic),
            total_unique_findings=len(self.static_findings) + len(self.dynamic_findings),
            deviation_evidence_items=deviation_rows,
            raw_gap_observations=raw_gap,
            unique_gap_observations=uniq_gap,
        )
