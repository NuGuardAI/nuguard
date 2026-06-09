"""Tests for new policy violation scenario builders.

Verifies that all Cognitive Policy dimensions produce at least one scenario:
- allowed_topics boundary
- hitl_tool_conditions
- rate_limits burst
- raw_sections probe
"""
from __future__ import annotations

import pytest

from nuguard.models.policy import CognitivePolicy, HitlToolCondition
from nuguard.redteam.scenarios.policy_violations import (
    build_allowed_topic_boundary,
    build_hitl_bypass,
    build_rate_limit_burst,
    build_raw_section_probe,
    build_restricted_action,
    build_restricted_topic,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario

_AGENT_ID = "agent-001"


class TestAllowedTopicBoundary:
    def test_produces_scenario(self) -> None:
        scenario = build_allowed_topic_boundary(
            _AGENT_ID, "cooking recipes", ["weapons", "drugs"]
        )
        assert isinstance(scenario, AttackScenario)
        assert scenario.chain is not None
        assert len(scenario.chain.steps) >= 2
        assert "cooking recipes" in scenario.title.lower() or "cooking" in scenario.title.lower()

    def test_uses_restricted_topic_in_pivot(self) -> None:
        scenario = build_allowed_topic_boundary(
            _AGENT_ID, "cooking recipes", ["weapons"]
        )
        # The second step should reference the restricted topic as a pivot
        second_payload = scenario.chain.steps[1].payload
        assert "weapons" in second_payload or "cooking" in second_payload

    def test_no_restricted_topics_defaults(self) -> None:
        scenario = build_allowed_topic_boundary(_AGENT_ID, "cooking", [])
        assert isinstance(scenario, AttackScenario)
        assert len(scenario.chain.steps) >= 2

    def test_target_node_ids(self) -> None:
        scenario = build_allowed_topic_boundary(_AGENT_ID, "travel", [])
        assert _AGENT_ID in scenario.target_node_ids


class TestRateLimitBurst:
    def test_produces_scenario(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "api_calls", 10)
        assert isinstance(scenario, AttackScenario)
        assert scenario.chain is not None

    def test_burst_capped_at_10(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "api_calls", 100)
        assert len(scenario.chain.steps) == 10

    def test_burst_is_limit_plus_one(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "queries", 3)
        assert len(scenario.chain.steps) == 4  # min(3+1, 10)

    def test_last_step_uses_llm_eval(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "requests", 5)
        last = scenario.chain.steps[-1]
        assert last.use_llm_eval is True

    def test_title_includes_key(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "api_calls", 10)
        assert "api_calls" in scenario.title


class TestRawSectionProbe:
    def test_produces_scenario(self) -> None:
        scenario = build_raw_section_probe(
            _AGENT_ID, "Data Retention", ["All data must be deleted after 30 days"]
        )
        assert isinstance(scenario, AttackScenario)
        assert len(scenario.chain.steps) >= 2

    def test_empty_bullets(self) -> None:
        scenario = build_raw_section_probe(_AGENT_ID, "Jurisdiction", [])
        assert isinstance(scenario, AttackScenario)

    def test_title_includes_section_name(self) -> None:
        scenario = build_raw_section_probe(_AGENT_ID, "Audit Logging", ["all ops logged"])
        assert "Audit Logging" in scenario.title


class TestGeneratorProducesAllDimensions:
    """Integration: ScenarioGenerator._policy_violation_scenarios covers all clauses."""

    def _make_sbom_with_agent(self) -> object:
        from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
        from nuguard.sbom.types import ComponentType
        import uuid
        node = Node(
            id=str(uuid.uuid4()),
            name="Main Agent",
            component_type=ComponentType.AGENT,
            confidence=1.0,
            metadata=NodeMetadata(),
        )
        return AiSbomDocument(target="test://local", nodes=[node], edges=[])

    def test_all_policy_dimensions_produce_scenarios(self) -> None:
        from nuguard.redteam.scenarios.generator import ScenarioGenerator

        sbom = self._make_sbom_with_agent()
        policy = CognitivePolicy(
            restricted_topics=["weapons"],
            restricted_actions=["delete all data"],
            hitl_triggers=["large transfer"],
            hitl_tool_conditions=[
                HitlToolCondition(tool_name="payment_tool", condition="amount > $500")
            ],
            allowed_topics=["cooking"],
            rate_limits={"api_calls": 5},
            raw_sections={"Custom Rules": ["do not share PII"]},
        )
        gen = ScenarioGenerator(sbom, policy)  # type: ignore[arg-type]
        scenarios = gen._policy_violation_scenarios()

        titles = [s.title for s in scenarios]

        # Each dimension must have produced at least one scenario
        assert any("weapons" in t or "Restricted Topic" in t for t in titles), "restricted_topics missing"
        assert any("delete all data" in t or "Restricted Action" in t for t in titles), "restricted_actions missing"
        assert any("large transfer" in t or "HITL Bypass" in t for t in titles), "hitl_triggers missing"
        assert any("payment_tool" in t or "HITL Bypass" in t for t in titles), "hitl_tool_conditions missing"
        assert any("cooking" in t or "Allowed Topic" in t for t in titles), "allowed_topics missing"
        assert any("api_calls" in t or "Rate Limit" in t for t in titles), "rate_limits missing"
        assert any("Custom Rules" in t or "Raw Policy" in t for t in titles), "raw_sections missing"
