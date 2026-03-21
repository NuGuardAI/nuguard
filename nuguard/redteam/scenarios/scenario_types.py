"""Attack scenario data classes."""
from __future__ import annotations

from pydantic import BaseModel, Field

from nuguard.models.exploit_chain import GoalType, ScenarioType, ExploitChain


class AttackScenario(BaseModel):
    scenario_id: str
    goal_type: GoalType
    scenario_type: ScenarioType
    title: str
    description: str
    target_node_ids: list[str] = Field(default_factory=list)
    precondition_summary: str = ""
    impact_score: float = 0.0
    chain: ExploitChain | None = None
