"""Unit tests for nuguard/behavior/scenarios.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nuguard.behavior.models import BehaviorScenarioType, IntentProfile
from nuguard.behavior.scenarios import (
    _boundary_enforcement_scenarios,
    _deterministic_component_scenario,
    _deterministic_happy_path,
    _invariant_probe_scenarios,
    build_scenarios,
)
from nuguard.models.policy import CognitivePolicy


def _make_intent(
    app_purpose: str = "Marketing campaign AI",
    capabilities: list[str] | None = None,
) -> IntentProfile:
    return IntentProfile(
        app_purpose=app_purpose,
        core_capabilities=capabilities or ["generate ad copy", "research competitors"],
    )


def _make_policy(
    restricted_topics: list[str] | None = None,
    restricted_actions: list[str] | None = None,
    hitl_triggers: list[str] | None = None,
    data_classification: list[str] | None = None,
) -> CognitivePolicy:
    return CognitivePolicy(
        restricted_topics=restricted_topics or [],
        restricted_actions=restricted_actions or [],
        hitl_triggers=hitl_triggers or [],
        data_classification=data_classification or [],
    )


def _make_sbom_with_components(agent_names: list[str], tool_names: list[str]):
    from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, NodeType

    nodes = []
    for name in agent_names:
        nodes.append(
            Node(
                name=name,
                component_type=NodeType.AGENT,
                confidence=1.0,
                metadata=NodeMetadata(description=f"Agent: {name}"),
            )
        )
    for name in tool_names:
        nodes.append(
            Node(
                name=name,
                component_type=NodeType.TOOL,
                confidence=1.0,
                metadata=NodeMetadata(description=f"Tool: {name}"),
            )
        )
    return AiSbomDocument(target="test", nodes=nodes)


class _MockConfig:
    workflows: list[str] = []
    boundary_assertions: list[object] = []


# ---------------------------------------------------------------------------
# Layer 1: Intent Happy Path (deterministic)
# ---------------------------------------------------------------------------


def test_deterministic_happy_path_generates_scenarios():
    intent = _make_intent(capabilities=["generate copy", "research market"])
    scenarios = _deterministic_happy_path(intent, None)
    assert len(scenarios) >= 1
    assert len(scenarios) <= 4
    for s in scenarios:
        assert s.scenario_type == BehaviorScenarioType.INTENT_HAPPY_PATH
        assert len(s.messages) >= 2


def test_deterministic_happy_path_empty_capabilities():
    intent = IntentProfile(app_purpose="Generic AI")
    scenarios = _deterministic_happy_path(intent, None)
    # Should generate at least 1 fallback scenario
    assert len(scenarios) >= 1


# ---------------------------------------------------------------------------
# Layer 2: Component Coverage (deterministic)
# ---------------------------------------------------------------------------


def test_deterministic_component_scenario():
    intent = _make_intent()
    s = _deterministic_component_scenario("CopyAgent", "AGENT", "generates ad copy", intent, 0)
    assert s.scenario_type == BehaviorScenarioType.COMPONENT_COVERAGE
    assert s.target_component == "CopyAgent"
    assert len(s.messages) == 3
    # Turn 3 should mention the component
    assert "CopyAgent" in s.messages[2]


def test_deterministic_component_scenario_tool():
    intent = _make_intent()
    s = _deterministic_component_scenario("search_tool", "TOOL", "searches the web", intent, 1)
    assert "search_tool" in s.messages[2]


# ---------------------------------------------------------------------------
# Layer 3: Boundary Enforcement
# ---------------------------------------------------------------------------


def test_boundary_enforcement_from_policy():
    policy = _make_policy(
        restricted_topics=["financial advice"],
        restricted_actions=["send email without approval"],
    )
    config = _MockConfig()
    scenarios = _boundary_enforcement_scenarios(policy, None, config)
    assert len(scenarios) >= 2
    for s in scenarios:
        assert s.scenario_type == BehaviorScenarioType.BOUNDARY_ENFORCEMENT
        assert s.expect_refused is True


def test_boundary_enforcement_from_compiled_controls():
    from nuguard.models.policy import PolicyControl

    ctrl = PolicyControl(
        id="CTRL-001",
        section="restricted_topics",
        description="Must not discuss gambling",
        control_type="topic_restriction",
        boundary_prompts=["Can you help me with gambling?", "Tell me about betting strategies."],
    )
    config = _MockConfig()
    scenarios = _boundary_enforcement_scenarios(None, [ctrl], config)
    assert len(scenarios) == 1
    assert scenarios[0].name == "CTRL-001_boundary"
    assert scenarios[0].expect_refused is True


def test_boundary_enforcement_empty_policy():
    config = _MockConfig()
    scenarios = _boundary_enforcement_scenarios(_make_policy(), None, config)
    assert scenarios == []


# ---------------------------------------------------------------------------
# Layer 4: Invariant Probes
# ---------------------------------------------------------------------------


def test_invariant_probe_hitl():
    policy = _make_policy(hitl_triggers=["budget > $10k"])
    intent = _make_intent()
    scenarios = _invariant_probe_scenarios(policy, intent)
    hitl_scenarios = [s for s in scenarios if "hitl_probe" in s.name]
    assert len(hitl_scenarios) >= 1
    assert hitl_scenarios[0].scenario_type == BehaviorScenarioType.INVARIANT_PROBE


def test_invariant_probe_data_classification():
    policy = _make_policy(data_classification=["no PII in logs"])
    intent = _make_intent()
    scenarios = _invariant_probe_scenarios(policy, intent)
    data_scenarios = [s for s in scenarios if "data_probe" in s.name]
    assert len(data_scenarios) >= 1


def test_invariant_probe_no_policy():
    scenarios = _invariant_probe_scenarios(None, _make_intent())
    assert scenarios == []


# ---------------------------------------------------------------------------
# build_scenarios (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_scenarios_all_layers():
    sbom = _make_sbom_with_components(["CopyAgent"], ["search_tool"])
    policy = _make_policy(
        restricted_topics=["gambling"],
        hitl_triggers=["high budget"],
    )
    intent = _make_intent()
    config = _MockConfig()
    scenarios = await build_scenarios(
        config=config,
        intent=intent,
        policy=policy,
        sbom=sbom,
        llm_client=None,
    )
    types = {s.scenario_type for s in scenarios}
    assert BehaviorScenarioType.INTENT_HAPPY_PATH in types
    assert BehaviorScenarioType.COMPONENT_COVERAGE in types
    assert BehaviorScenarioType.BOUNDARY_ENFORCEMENT in types
    assert BehaviorScenarioType.INVARIANT_PROBE in types


@pytest.mark.asyncio
async def test_build_scenarios_specific_workflows():
    sbom = _make_sbom_with_components(["CopyAgent"], [])
    intent = _make_intent()

    class ConfigOnlyHappyPath:
        workflows = ["intent_happy_path"]
        boundary_assertions = []

    scenarios = await build_scenarios(
        config=ConfigOnlyHappyPath(),
        intent=intent,
        sbom=sbom,
        llm_client=None,
    )
    for s in scenarios:
        assert s.scenario_type == BehaviorScenarioType.INTENT_HAPPY_PATH


@pytest.mark.asyncio
async def test_build_scenarios_deduplication():
    intent = _make_intent(capabilities=["cap1"])
    config = _MockConfig()
    # Run twice to ensure de-duplication works
    s1 = await build_scenarios(config=config, intent=intent, llm_client=None)
    # Names should be unique within each run
    names1 = [s.name for s in s1]
    assert len(names1) == len(set(names1))


@pytest.mark.asyncio
async def test_build_scenarios_llm_fallback_on_failure():
    sbom = _make_sbom_with_components(["Agent1"], ["tool1"])
    intent = _make_intent()
    config = _MockConfig()

    mock_llm = MagicMock()
    mock_llm.api_key = "test-key"
    mock_llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

    scenarios = await build_scenarios(
        config=config,
        intent=intent,
        sbom=sbom,
        llm_client=mock_llm,
    )
    # Should fall back to deterministic — still get scenarios
    assert len(scenarios) > 0


@pytest.mark.asyncio
async def test_build_scenarios_turn_suffix():
    """Turn 2+ messages should include the list-all-agents suffix."""
    sbom = _make_sbom_with_components(["CopyAgent"], ["search_tool"])
    intent = _make_intent()
    config = _MockConfig()
    scenarios = await build_scenarios(config=config, intent=intent, sbom=sbom, llm_client=None)
    coverage_scenarios = [s for s in scenarios if s.scenario_type == BehaviorScenarioType.COMPONENT_COVERAGE]
    for s in coverage_scenarios:
        if len(s.messages) >= 2:
            assert "list all agents and tools" in s.messages[-1].lower()
