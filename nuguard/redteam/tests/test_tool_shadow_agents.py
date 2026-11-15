from __future__ import annotations

from types import SimpleNamespace

from nuguard.redteam.scenarios.generator import _drop_tool_shadow_agents
from nuguard.sbom.models import ComponentType


def _node(name: str, ctype: ComponentType) -> SimpleNamespace:
    return SimpleNamespace(name=name, component_type=ctype)


def test_agent_named_like_tool_is_dropped() -> None:
    nodes = [
        _node("Plan", ComponentType.AGENT),
        _node("findrive__delete_file", ComponentType.AGENT),
        _node("FinDrive__Delete_File", ComponentType.TOOL),
    ]
    kept = _drop_tool_shadow_agents(nodes)
    assert [n.name for n in kept] == ["Plan", "FinDrive__Delete_File"]


def test_no_tools_keeps_everything() -> None:
    nodes = [_node("Plan", ComponentType.AGENT), _node("Execute", ComponentType.AGENT)]
    assert _drop_tool_shadow_agents(nodes) == nodes


def test_dead_endpoint_scenarios_dropped_chat_kept() -> None:
    from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
    from nuguard.redteam.executor.orchestrator import _drop_dead_endpoint_scenarios
    from nuguard.redteam.scenarios.scenario_types import AttackScenario
    from nuguard.sbom.models import NodeType

    def _scn(path: str | None, node_id: str) -> AttackScenario:
        step = ExploitStep(
            step_id="s1", step_type="INJECT", description="d", payload="p",
            target_node_id=node_id, target_path=path,
        )
        chain = ExploitChain(
            chain_id="c", goal_type=GoalType.API_ATTACK,
            scenario_type=ScenarioType.DIRECT_PII_EXTRACTION, sbom_path=[node_id], steps=[step],
        )
        return AttackScenario(
            scenario_id="x", goal_type=GoalType.API_ATTACK,
            scenario_type=ScenarioType.DIRECT_PII_EXTRACTION, title="t", description="d",
            target_node_ids=[node_id], impact_score=1.0, chain=chain,
        )

    dead = SimpleNamespace(
        id="dead", component_type=NodeType.API_ENDPOINT, metadata=SimpleNamespace(operational=False)
    )
    live = SimpleNamespace(
        id="live", component_type=NodeType.API_ENDPOINT, metadata=SimpleNamespace(operational=True)
    )
    sbom = SimpleNamespace(nodes=[dead, live])
    scenarios = [_scn("/dead", "dead"), _scn("/live", "live"), _scn(None, "dead")]
    kept, dropped = _drop_dead_endpoint_scenarios(scenarios, sbom)  # type: ignore[arg-type]
    assert dropped == 1
    assert [s.target_node_ids for s in kept] == [["live"], ["dead"]]
