"""Regression tests for Fix 4 (part 1): entry-endpoint dedup key must not
collapse distinct topic/framing variants of the same scenario family.

Root cause: ``ScenarioGenerator._dedup_by_entry_endpoint`` fingerprinted
scenarios on ``(goal_type, scenario_type, title.split(" — ")[0])``. Every
"Restricted Topic Probe — <topic>" scenario shares that prefix regardless of
topic/framing, so when qualifying API_ENDPOINT -> AGENT edges exist (making
``_compute_entry_agents()`` non-empty), the dedup pass collapsed ALL topic
variants into one group and kept only ``_MAX_AGENTS_PER_GOAL`` (2) of them —
silently discarding coverage. The fix fingerprints on the FULL title instead,
so distinct topics/framings are never collapsed together, while scenarios
with genuinely identical titles (differing only in which agent — entry vs.
sub-agent — they target) still dedup as intended.
"""
from __future__ import annotations

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument, Edge, Node
from nuguard.sbom.types import ComponentType, RelationshipType


def _sbom_with_entry_agent() -> tuple[AiSbomDocument, str]:
    """A minimal SBOM with one API_ENDPOINT -> AGENT CALLS edge, the
    structural signal ``_compute_entry_agents()`` requires to mark that
    agent as an "entry agent"."""
    endpoint = Node(name="POST /chat", component_type=ComponentType.API_ENDPOINT, confidence=1.0)
    agent = Node(name="concierge-agent", component_type=ComponentType.AGENT, confidence=1.0)
    sbom = AiSbomDocument(
        target="unit-test",
        nodes=[endpoint, agent],
        edges=[
            Edge(
                source=endpoint.id,
                target=agent.id,
                relationship_type=RelationshipType.CALLS,
            )
        ],
    )
    return sbom, str(agent.id)


def _topic_probe_scenario(title: str, target_node_id: str) -> AttackScenario:
    return AttackScenario(
        scenario_id=title,
        goal_type=GoalType.POLICY_VIOLATION,
        scenario_type=ScenarioType.OFF_TOPIC_PROBE,
        title=title,
        description="restricted topic probe regression scenario",
        target_node_ids=[target_node_id],
        impact_score=5.0,
    )


def test_compute_entry_agents_finds_endpoint_called_agent() -> None:
    sbom, agent_id = _sbom_with_entry_agent()
    gen = ScenarioGenerator(sbom)
    assert agent_id in gen._compute_entry_agents()


def test_dedup_preserves_distinct_topic_variants_targeting_entry_agent() -> None:
    """Two 'Restricted Topic Probe' scenarios with different topics, both
    targeting the entry agent, must BOTH survive dedup — not collapse into
    one because they share the pre-' — ' title prefix."""
    sbom, agent_id = _sbom_with_entry_agent()
    gen = ScenarioGenerator(sbom)
    entry_agents = gen._compute_entry_agents()
    assert agent_id in entry_agents

    s1 = _topic_probe_scenario("Restricted Topic Probe — self-harm (explicit)", agent_id)
    s2 = _topic_probe_scenario("Restricted Topic Probe — violence (implicit, curious)", agent_id)

    result = gen._dedup_by_entry_endpoint([s1, s2], entry_agents)

    titles = {s.title for s in result}
    assert titles == {s1.title, s2.title}


def test_dedup_still_collapses_truly_identical_titles_preferring_entry_agent() -> None:
    """The dedup pass's actual intended purpose — collapsing genuinely
    identical scenarios that differ only in entry-vs-sub-agent targeting —
    must still work when the full title matches."""
    sbom, agent_id = _sbom_with_entry_agent()
    gen = ScenarioGenerator(sbom)
    entry_agents = gen._compute_entry_agents()

    same_title = "Restricted Topic Probe — self-harm (explicit)"
    entry_targeted = _topic_probe_scenario(same_title, agent_id)
    sub_agent_targeted = _topic_probe_scenario(same_title, "sub-agent-node-id")

    result = gen._dedup_by_entry_endpoint(
        [entry_targeted, sub_agent_targeted], entry_agents
    )

    # Only the entry-agent-targeted scenario survives — the sub-agent
    # duplicate is dropped, exactly as the dedup pass intends.
    assert len(result) == 1
    assert result[0].target_node_ids == [agent_id]
