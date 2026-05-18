"""Attack scenario data classes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType

if TYPE_CHECKING:
    from nuguard.redteam.models.guided_conversation import GuidedConversation


class AttackScenario(BaseModel):
    scenario_id: str
    goal_type: GoalType
    scenario_type: ScenarioType
    title: str
    description: str
    target_node_ids: list[str] = Field(default_factory=list)
    precondition_summary: str = ""
    impact_score: float = 0.0
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
