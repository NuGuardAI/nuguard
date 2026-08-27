"""Attack scenario data classes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType
from nuguard.redteam.catalog.taxonomy import (
    DeliveryChannel,
    EvidenceType,
    SafeExecution,
    SinkType,
    SourceTrust,
)

if TYPE_CHECKING:
    from nuguard.redteam.models.guided_conversation import GuidedConversation


class AttackScenario(BaseModel):
    scenario_id: str
    goal_type: GoalType
    scenario_type: ScenarioType
    title: str
    description: str
    target_node_ids: list[str] = Field(default_factory=list)
    # Names of tools reachable from the target agent (CALLS edges in the SBOM).
    # Populated by the scenario generator and passed into the LLM prompt builder
    # so generated variants can name specific tools (e.g. Gmail, Calendar) rather
    # than emit generic "show me account data" framings.
    target_tool_names: list[str] = Field(default_factory=list)
    precondition_summary: str = ""
    impact_score: float = 0.0
    # Escalation phase (1-9, see scenarios/generator.py _ATTACK_PHASE) — recon
    # before boundary-mapping before destructive/high-impact actions. Set by
    # the generator at sort time and consumed by the orchestrator to batch
    # dispatch by phase rather than firing the whole catalog concurrently.
    attack_phase: int = 5
    # ── Catalog taxonomy (docs/llm-runs/Red-team-new-design.md) ──────────────
    # Populated when a scenario is produced by the catalog builder factories.
    # All optional/defaulted so the dozens of direct AttackScenario(...) call
    # sites in the legacy per-family builders remain valid unchanged.
    catalog_id: str = ""                       # stable ID e.g. "C01" / "T03"
    category: str = ""                         # ScenarioCategory.value
    delivery_channel: DeliveryChannel | None = None
    source_trust: SourceTrust | None = None
    sink_type: SinkType | None = None
    evidence_types: list[EvidenceType] = Field(default_factory=list)
    safe_execution: SafeExecution | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    expected_control: str = ""
    # Set when a catalog spec was matched but could not be instantiated (e.g.
    # a required app feature is absent); surfaced in the coverage report.
    skipped_reason: str = ""
    # Static chain (pre-built payloads) — mutually exclusive with guided_conversation.
    chain: ExploitChain | None = None
    # Dynamic guided conversation — generated in real time from agent responses.
    # When set, the orchestrator routes to GuidedAttackExecutor instead of AttackExecutor.
    guided_conversation: "GuidedConversation | None" = None


# Resolve the forward reference so pydantic can fully validate the model.
# This import is intentionally placed after the class definition to avoid
# circular imports during module loading.
from nuguard.redteam.models.guided_conversation import GuidedConversation  # noqa: E402

AttackScenario.model_rebuild(_types_namespace={"GuidedConversation": GuidedConversation})
