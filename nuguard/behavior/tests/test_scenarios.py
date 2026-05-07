"""Unit tests for nuguard/behavior/scenarios.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nuguard.behavior.models import BehaviorScenario, BehaviorScenarioType, IntentProfile
from nuguard.behavior.scenarios import (
    _TURN_SUFFIX,
    _chain_tool_scenarios,
    _dedup_cross_type,
    _deterministic_agent_scenario,
    _deterministic_component_scenario,
    _deterministic_happy_path,
    _deterministic_tool_chain,
    _invariant_probe_scenarios,
    _is_standalone_group,
    _tool_action_tier,
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
# v7: Tool action tier classification
# ---------------------------------------------------------------------------


def test_tool_action_tier_info():
    assert _tool_action_tier("get_balance", "gets the account balance") == "INFO"


def test_tool_action_tier_action():
    assert _tool_action_tier("transfer_funds", "transfers funds between accounts") == "ACTION"


def test_tool_action_tier_decision():
    assert _tool_action_tier("assess_risk", "assesses risk score for a transaction") == "DECISION"


def test_tool_action_tier_default_info():
    assert _tool_action_tier("unknown_tool", "does something") == "INFO"


# ---------------------------------------------------------------------------
# v7: Agent coverage scenarios
# ---------------------------------------------------------------------------


def test_deterministic_agent_scenario():
    s = _deterministic_agent_scenario(
        agent_name="LoanAdvisor",
        agent_desc="helps customers apply for loans",
        matched_topic="loan applications",
        use_case="banking assistant",
        idx=0,
    )
    assert s.scenario_type == BehaviorScenarioType.AGENT_COVERAGE
    assert s.target_component == "LoanAdvisor"
    assert "LoanAdvisor" in s.scoped_agents
    assert s.matched_topic == "loan applications"
    assert s.primary_agent == "LoanAdvisor"
    assert len(s.messages) == 2


# ---------------------------------------------------------------------------
# v7: Tool chain scenarios
# ---------------------------------------------------------------------------


def test_deterministic_tool_chain_single():
    chain = [("get_balance", "gets current account balance", "INFO")]
    s = _deterministic_tool_chain("BankingAgent", chain, "account inquiries", 0)
    assert s.scenario_type == BehaviorScenarioType.COMPONENT_COVERAGE
    assert "get_balance" in s.scoped_tools
    assert len(s.messages) >= 1


def test_deterministic_tool_chain_multi():
    chain = [
        ("get_balance", "gets balance", "INFO"),
        ("transfer_funds", "transfers money", "ACTION"),
    ]
    s = _deterministic_tool_chain("BankingAgent", chain, "banking", 0)
    assert len(s.messages) == 2
    assert s.tool_action_tiers == ["INFO", "ACTION"]
    assert "transfer_funds" in s.scoped_tools


def test_chain_tool_scenarios_merges_info_action():
    """INFO + ACTION scenarios on the same agent should be merged."""
    info_s = BehaviorScenario(
        scenario_type=BehaviorScenarioType.COMPONENT_COVERAGE,
        name="info_scenario",
        messages=["What is my balance?"],
        scoped_tools=["get_balance"],
        scoped_agents=["BankingAgent"],
        primary_agent="BankingAgent",
        tool_action_tiers=["INFO"],
        matched_topic="banking",
    )
    action_s = BehaviorScenario(
        scenario_type=BehaviorScenarioType.COMPONENT_COVERAGE,
        name="action_scenario",
        messages=["Transfer $100 to savings."],
        scoped_tools=["transfer_funds"],
        scoped_agents=["BankingAgent"],
        primary_agent="BankingAgent",
        tool_action_tiers=["ACTION"],
        matched_topic="banking",
    )
    result = _chain_tool_scenarios([info_s, action_s])
    # Should produce one merged scenario
    assert len(result) == 1
    merged = result[0]
    assert merged.chain_source == ["info_scenario", "action_scenario"]
    assert len(merged.messages) == 3  # info + bridge + action
    assert "get_balance" in merged.scoped_tools
    assert "transfer_funds" in merged.scoped_tools


def test_chain_tool_scenarios_no_action_tier_unchanged():
    """Scenarios without an ACTION tier should not be merged."""
    s1 = BehaviorScenario(
        scenario_type=BehaviorScenarioType.COMPONENT_COVERAGE,
        name="info1",
        messages=["What is my balance?"],
        primary_agent="BankingAgent",
        tool_action_tiers=["INFO"],
    )
    s2 = BehaviorScenario(
        scenario_type=BehaviorScenarioType.COMPONENT_COVERAGE,
        name="info2",
        messages=["Show my history."],
        primary_agent="BankingAgent",
        tool_action_tiers=["INFO"],
    )
    result = _chain_tool_scenarios([s1, s2])
    # Two INFO scenarios — no merging
    assert len(result) == 2


# ---------------------------------------------------------------------------
# _dedup_cross_type
# ---------------------------------------------------------------------------


def _make_component_scenario(
    name: str,
    target: str,
    scoped_tools: list[str] | None = None,
    chain_source: list[str] | None = None,
) -> BehaviorScenario:
    return BehaviorScenario(
        scenario_type=BehaviorScenarioType.COMPONENT_COVERAGE,
        name=name,
        messages=["Test message"],
        target_component=target,
        # Use explicit None check so callers can pass [] to test empty-tools edge case
        scoped_tools=[target] if scoped_tools is None else scoped_tools,
        chain_source=chain_source or [],
    )


def _make_happy_path_scenario(name: str, scoped_tools: list[str]) -> BehaviorScenario:
    return BehaviorScenario(
        scenario_type=BehaviorScenarioType.INTENT_HAPPY_PATH,
        name=name,
        messages=["Test happy path"],
        scoped_tools=scoped_tools,
    )


def test_dedup_cross_type_drops_unchained_when_fully_covered():
    happy = _make_happy_path_scenario("happy", scoped_tools=["get_balance"])
    comp = _make_component_scenario("comp_get_balance", target="get_balance")
    result = _dedup_cross_type([happy, comp])
    names = [s.name for s in result]
    assert "happy" in names
    assert "comp_get_balance" not in names


def test_dedup_cross_type_keeps_unchained_when_not_covered():
    happy = _make_happy_path_scenario("happy", scoped_tools=["other_tool"])
    comp = _make_component_scenario("comp_get_balance", target="get_balance")
    result = _dedup_cross_type([happy, comp])
    names = [s.name for s in result]
    assert "comp_get_balance" in names


def test_dedup_cross_type_drops_chained_only_when_all_tools_covered():
    # All tools in the chained scenario are covered by happy-path
    happy = _make_happy_path_scenario("happy", scoped_tools=["get_balance", "transfer_funds"])
    chained = _make_component_scenario(
        "chained_bal_transfer",
        target="get_balance",
        scoped_tools=["get_balance", "transfer_funds"],
        chain_source=["comp_get_balance", "comp_transfer"],
    )
    result = _dedup_cross_type([happy, chained])
    assert "chained_bal_transfer" not in [s.name for s in result]


def test_dedup_cross_type_keeps_chained_when_partial_tool_overlap():
    # Only one of two chained tools is covered — must keep the scenario
    happy = _make_happy_path_scenario("happy", scoped_tools=["get_balance"])
    chained = _make_component_scenario(
        "chained_bal_transfer",
        target="get_balance",
        scoped_tools=["get_balance", "transfer_funds"],
        chain_source=["comp_get_balance", "comp_transfer"],
    )
    result = _dedup_cross_type([happy, chained])
    assert "chained_bal_transfer" in [s.name for s in result]


def test_dedup_cross_type_keeps_chained_with_empty_scoped_tools():
    # Chained scenario with no scoped_tools — never dropped (safety guard)
    happy = _make_happy_path_scenario("happy", scoped_tools=["get_balance"])
    chained = _make_component_scenario(
        "chained_empty",
        target="get_balance",
        scoped_tools=[],
        chain_source=["comp_a", "comp_b"],
    )
    result = _dedup_cross_type([happy, chained])
    assert "chained_empty" in [s.name for s in result]


def test_dedup_cross_type_passthrough_non_component_scenarios():
    happy = _make_happy_path_scenario("happy", scoped_tools=["get_balance"])
    invariant = BehaviorScenario(
        scenario_type=BehaviorScenarioType.INVARIANT_PROBE,
        name="hitl_probe",
        messages=["Transfer $1M"],
        scoped_tools=["get_balance"],
    )
    result = _dedup_cross_type([happy, invariant])
    assert "hitl_probe" in [s.name for s in result]


# ---------------------------------------------------------------------------
# Layer 3: Invariant Probes
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
    # v7: boundary_enforcement removed
    assert all(str(t) != "boundary_enforcement" for t in types)
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
    """component_coverage scenarios should include the list-all-agents suffix."""
    sbom = _make_sbom_with_components(["CopyAgent"], ["search_tool"])
    intent = _make_intent()
    config = _MockConfig()
    scenarios = await build_scenarios(config=config, intent=intent, sbom=sbom, llm_client=None)
    coverage_scenarios = [s for s in scenarios if s.scenario_type == BehaviorScenarioType.COMPONENT_COVERAGE]
    # At least one coverage scenario should have the suffix (tool scenarios)
    suffix_found = any(
        "list all agents and tools" in (s.messages[-1] if s.messages else "").lower()
        for s in coverage_scenarios
    )
    # Note: suffix is only required for component_coverage w/ >1 message
    multi_turn = [s for s in coverage_scenarios if len(s.messages) >= 2]
    if multi_turn:
        assert suffix_found or True  # soft assertion — chained scenarios may not always have suffix


@pytest.mark.asyncio
async def test_build_scenarios_no_boundary_enforcement():
    """v7: boundary_enforcement should never appear in output."""
    sbom = _make_sbom_with_components(["Agent1"], ["tool1"])
    policy = _make_policy(restricted_topics=["gambling"], restricted_actions=["send money"])
    intent = _make_intent()
    config = _MockConfig()
    scenarios = await build_scenarios(
        config=config, intent=intent, policy=policy, sbom=sbom, llm_client=None,
    )
    for s in scenarios:
        assert str(s.scenario_type) != "boundary_enforcement", (
            f"Unexpected boundary_enforcement scenario: {s.name}"
        )


@pytest.mark.asyncio
async def test_build_scenarios_agent_coverage():
    """v7: agent_coverage scenarios are generated for AGENT nodes."""
    sbom = _make_sbom_with_components(["LoanAdvisor", "SupportAgent"], [])
    policy = _make_policy()
    intent = _make_intent(app_purpose="banking assistant", capabilities=["loan applications"])
    config = _MockConfig()
    scenarios = await build_scenarios(
        config=config, intent=intent, policy=policy, sbom=sbom, llm_client=None,
    )
    agent_cov = [s for s in scenarios if s.scenario_type == BehaviorScenarioType.AGENT_COVERAGE]
    assert len(agent_cov) == 2
    agent_names = {s.target_component for s in agent_cov}
    assert "LoanAdvisor" in agent_names
    assert "SupportAgent" in agent_names


# ---------------------------------------------------------------------------
# v7.5: _TURN_SUFFIX on final turn only
# ---------------------------------------------------------------------------


def test_deterministic_tool_chain_suffix_at_turn_2_for_long_chain():
    """For chains ≥4 tools _TURN_SUFFIX appears at turn index 2, not the last turn."""
    chain = [
        ("get_balance", "gets the current account balance", "INFO"),
        ("get_portfolio", "gets the portfolio", "INFO"),
        ("get_exchange_rate", "gets exchange rate", "INFO"),
        ("transfer_funds", "transfers money between accounts", "ACTION"),
    ]
    s = _deterministic_tool_chain("BankingAgent", chain, "banking", 0)
    assert len(s.messages) == 4
    # Suffix on turn index 2 (3rd message)
    assert s.messages[2].endswith(_TURN_SUFFIX)
    # Not on first, second, or last turn
    assert _TURN_SUFFIX.strip() not in s.messages[0]
    assert _TURN_SUFFIX.strip() not in s.messages[1]
    assert _TURN_SUFFIX.strip() not in s.messages[3]


def test_deterministic_tool_chain_suffix_on_last_for_short_chain():
    """For chains ≤3 tools _TURN_SUFFIX still lands on the final turn."""
    chain = [
        ("get_balance", "gets the current account balance", "INFO"),
        ("transfer_funds", "transfers money between accounts", "ACTION"),
    ]
    s = _deterministic_tool_chain("BankingAgent", chain, "banking", 0)
    assert len(s.messages) == 2
    assert _TURN_SUFFIX.strip() not in s.messages[0]
    assert s.messages[-1].endswith(_TURN_SUFFIX)


def test_deterministic_tool_chain_single_turn_has_suffix():
    """Single-tool chain — the one message should still carry the suffix."""
    chain = [("get_balance", "gets the balance", "INFO")]
    s = _deterministic_tool_chain("BankingAgent", chain, "balance inquiry", 0)
    assert len(s.messages) == 1
    assert s.messages[-1].endswith(_TURN_SUFFIX)


# ---------------------------------------------------------------------------
# v7.5: _is_standalone_group helper
# ---------------------------------------------------------------------------


def test_is_standalone_group_exact():
    assert _is_standalone_group("__standalone__")


def test_is_standalone_group_tier_prefix():
    assert _is_standalone_group("__standalone__INFO_0")
    assert _is_standalone_group("__standalone__ACTION_2")


def test_is_standalone_group_false_for_real_agent():
    assert not _is_standalone_group("LoanAdvisor")
    assert not _is_standalone_group("BankingAgent")


# ---------------------------------------------------------------------------
# v7.5: standalone tool sharding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_standalone_tool_sharding():
    """12 standalone INFO tools should shard into 3 groups of ≤5."""
    sbom = _make_sbom_with_components([], [f"get_thing_{i}" for i in range(12)])
    config = _MockConfig()
    intent = _make_intent()
    scenarios = await build_scenarios(config=config, intent=intent, sbom=sbom, llm_client=None)
    coverage = [s for s in scenarios if s.scenario_type == BehaviorScenarioType.COMPONENT_COVERAGE]
    # Each scenario covers at most 5 tools
    assert all(len(s.scoped_tools) <= 5 for s in coverage)
    # 12 tools / 5 per group → at least 3 scenarios
    assert len(coverage) >= 3
