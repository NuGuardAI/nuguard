"""Tests for behavior scenario generation — focusing on coverage of AGENT nodes."""
from __future__ import annotations

import uuid

import pytest

from nuguard.behavior.models import BehaviorScenarioType, IntentProfile
from nuguard.behavior.scenarios import (
    _delegates_to_scenarios,
    _endpoint_coverage_scenarios,
    _guardrail_path_scenarios,
    _hitl_probe_scenarios,
    _policy_fragment,
    _policy_topic_coverage_scenarios,
    _tool_coverage_scenarios,
)
from nuguard.models.policy import CognitivePolicy
from nuguard.sbom.models import (
    AiSbomDocument,
    Edge,
    EdgeRelationshipType,
    Node,
    NodeMetadata,
    NodeType,
)

_NS = uuid.NAMESPACE_URL


def _make_sbom(
    agent_names: list[str],
    tool_names: list[str],
) -> AiSbomDocument:
    """Build a minimal SBOM with AGENT→TOOL CALLS edges."""
    nodes: list[Node] = []
    edges: list[Edge] = []

    agent_ids: dict[str, uuid.UUID] = {}
    for name in agent_names:
        nid = uuid.uuid5(_NS, f"agent/{name}")
        agent_ids[name] = nid
        nodes.append(Node(
            id=nid,
            name=name,
            component_type=NodeType.AGENT,
            confidence=0.9,
            metadata=NodeMetadata(description=f"{name} handles customer requests"),
        ))

    for i, name in enumerate(tool_names):
        tid = uuid.uuid5(_NS, f"tool/{name}")
        nodes.append(Node(
            id=tid,
            name=name,
            component_type=NodeType.TOOL,
            confidence=0.9,
            metadata=NodeMetadata(description=f"{name} tool"),
        ))
        # Wire first agent → all tools
        if agent_names:
            edges.append(Edge(
                source=agent_ids[agent_names[0]],
                target=tid,
                relationship_type=EdgeRelationshipType.CALLS,
            ))

    return AiSbomDocument(target="./test-app", nodes=nodes, edges=edges)


def _make_intent(purpose: str = "test app") -> IntentProfile:
    return IntentProfile(app_purpose=purpose, core_capabilities=["help users"])


# ---------------------------------------------------------------------------
# Agent-level scenario emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_coverage_emits_agent_scenario():
    """_tool_coverage_scenarios must emit at least one AGENT-typed scenario."""
    sbom = _make_sbom(["MyAgent"], ["tool_a", "tool_b"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_scenarios = [
        s for s in scenarios
        if getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
        or s.target_component_type == "AGENT"
    ]
    assert agent_scenarios, "Expected at least one AGENT-type scenario from _tool_coverage_scenarios"


@pytest.mark.asyncio
async def test_tool_coverage_agent_scenario_targets_correct_agent():
    """The emitted agent scenario must target the actual agent name."""
    sbom = _make_sbom(["MyAgent"], ["tool_a"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_scenarios = [
        s for s in scenarios
        if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
            or s.target_component_type == "AGENT")
    ]
    assert any(s.target_component == "MyAgent" for s in agent_scenarios)


@pytest.mark.asyncio
async def test_tool_coverage_agent_scenario_scoped_correctly():
    """Agent scenario must set scoped_agents=[agent_name] and scoped_tools=[]."""
    sbom = _make_sbom(["MyAgent"], ["tool_a"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_sc = next(
        (s for s in scenarios
         if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
             or s.target_component_type == "AGENT")
         and s.target_component == "MyAgent"),
        None,
    )
    assert agent_sc is not None
    assert list(agent_sc.scoped_agents) == ["MyAgent"]
    assert list(agent_sc.scoped_tools) == []


@pytest.mark.asyncio
async def test_tool_coverage_no_agent_scenario_for_standalone_group():
    """Standalone tool groups (no agent) must not produce an AGENT-type scenario."""
    # SBOM with a tool that has no CALLS edge from any agent — becomes standalone
    tid = uuid.uuid5(_NS, "tool/orphan_tool")
    sbom = AiSbomDocument(
        target="./test-app",
        nodes=[Node(id=tid, name="orphan_tool", component_type=NodeType.TOOL, confidence=0.9)],
        edges=[],
    )
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_scenarios = [
        s for s in scenarios
        if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
            or s.target_component_type == "AGENT")
    ]
    assert not agent_scenarios, "Standalone tool groups must not emit AGENT-type scenarios"


@pytest.mark.asyncio
async def test_tool_coverage_multiple_agents_each_get_agent_scenario():
    """Each distinct real agent in the SBOM must get its own agent-level scenario."""
    agent1_id = uuid.uuid5(_NS, "agent/Agent1")
    agent2_id = uuid.uuid5(_NS, "agent/Agent2")
    tool1_id = uuid.uuid5(_NS, "tool/tool1")
    tool2_id = uuid.uuid5(_NS, "tool/tool2")

    sbom = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(id=agent1_id, name="Agent1", component_type=NodeType.AGENT, confidence=0.9),
            Node(id=agent2_id, name="Agent2", component_type=NodeType.AGENT, confidence=0.9),
            Node(id=tool1_id, name="tool1", component_type=NodeType.TOOL, confidence=0.9),
            Node(id=tool2_id, name="tool2", component_type=NodeType.TOOL, confidence=0.9),
        ],
        edges=[
            Edge(source=agent1_id, target=tool1_id, relationship_type=EdgeRelationshipType.CALLS),
            Edge(source=agent2_id, target=tool2_id, relationship_type=EdgeRelationshipType.CALLS),
        ],
    )
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_targets = {
        s.target_component
        for s in scenarios
        if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
            or s.target_component_type == "AGENT")
    }
    assert "Agent1" in agent_targets
    assert "Agent2" in agent_targets


@pytest.mark.asyncio
async def test_tool_coverage_agent_scenario_is_agent_coverage_type():
    """Agent scenarios emitted by _tool_coverage_scenarios use AGENT_COVERAGE type
    (from _deterministic_agent_scenario), which the runner handles correctly."""
    sbom = _make_sbom(["MyAgent"], ["tool_a"])
    scenarios = await _tool_coverage_scenarios(sbom, _make_intent(), None, None)

    agent_sc = next(
        (s for s in scenarios
         if (getattr(s.target_component_type, "value", str(s.target_component_type)).upper() == "AGENT"
             or s.target_component_type == "AGENT")),
        None,
    )
    assert agent_sc is not None
    stype = getattr(agent_sc.scenario_type, "value", str(agent_sc.scenario_type))
    assert stype == BehaviorScenarioType.AGENT_COVERAGE.value


# ---------------------------------------------------------------------------
# Guardrail-path scenario generation
# ---------------------------------------------------------------------------


def _make_guardrail_sbom(blocked_actions: list[str] | None = None) -> AiSbomDocument:
    gid = uuid.uuid5(_NS, "guardrail/ContentGuard")
    tid = uuid.uuid5(_NS, "tool/TransferTool")
    guardrail = Node(
        id=gid,
        name="ContentGuard",
        component_type=NodeType.GUARDRAIL,
        confidence=1.0,
        metadata=NodeMetadata(
            description="Content safety guardrail",
            rules_excerpt="Blocks financial transfers",
            blocked_actions=blocked_actions or ["transfer funds", "send money"],
            refusal_style="hard_block",
        ),
    )
    tool = Node(
        id=tid,
        name="TransferTool",
        component_type=NodeType.TOOL,
        confidence=1.0,
        metadata=NodeMetadata(description="handles financial transfers"),
    )
    return AiSbomDocument(
        target="./app",
        nodes=[guardrail, tool],
        edges=[Edge(source=gid, target=tid, relationship_type=EdgeRelationshipType.PROTECTS)],
    )


def test_guardrail_path_emits_invariant_probe():
    """_guardrail_path_scenarios emits GUARDRAIL_PROBE for each blocked_action."""
    sbom = _make_guardrail_sbom(["transfer funds"])
    intent = _make_intent("banking assistant")
    scenarios = _guardrail_path_scenarios(sbom, intent)
    assert scenarios, "Expected at least one guardrail scenario"
    for sc in scenarios:
        stype = getattr(sc.scenario_type, "value", str(sc.scenario_type))
        assert stype == BehaviorScenarioType.GUARDRAIL_PROBE.value


def test_guardrail_path_sets_expect_not_refused():
    # Guardrail path scenarios now verify positive pass-through (allowed topics),
    # so expect_refused must be False on all emitted probes.
    sbom = _make_guardrail_sbom(["transfer funds"])
    scenarios = _guardrail_path_scenarios(sbom, _make_intent())
    assert all(not s.expect_refused for s in scenarios)


def test_guardrail_path_sets_scoped_guardrail():
    sbom = _make_guardrail_sbom(["send money"])
    scenarios = _guardrail_path_scenarios(sbom, _make_intent())
    assert all(s.scoped_guardrail == "ContentGuard" for s in scenarios)


def test_guardrail_path_two_turn_messages():
    sbom = _make_guardrail_sbom(["transfer funds"])
    scenarios = _guardrail_path_scenarios(sbom, _make_intent())
    for sc in scenarios:
        assert len(sc.messages) == 2, "Guardrail probe must be 2 turns"


def test_guardrail_path_no_scenarios_for_empty_guardrail():
    # A guardrail with no rules_excerpt and no policy allowed_topics has no
    # useful context to probe — no scenarios should be generated.
    gid = uuid.uuid5(_NS, "guardrail/EmptyGuard")
    guardrail = Node(
        id=gid,
        name="EmptyGuard",
        component_type=NodeType.GUARDRAIL,
        confidence=1.0,
        metadata=NodeMetadata(),
    )
    sbom = AiSbomDocument(target="./app", nodes=[guardrail], edges=[])
    # No policy passed → no allowed_topics, and guardrail has no rules_excerpt → skip.
    scenarios = _guardrail_path_scenarios(sbom, _make_intent())
    assert scenarios == []


# ---------------------------------------------------------------------------
# DELEGATES_TO scenario generation
# ---------------------------------------------------------------------------


def _make_delegation_sbom() -> AiSbomDocument:
    src_id = uuid.uuid5(_NS, "agent/SrcAgent")
    tgt_id = uuid.uuid5(_NS, "agent/TgtAgent")
    src = Node(
        id=src_id,
        name="SrcAgent",
        component_type=NodeType.AGENT,
        confidence=1.0,
        metadata=NodeMetadata(description="orchestrator agent"),
    )
    tgt = Node(
        id=tgt_id,
        name="TgtAgent",
        component_type=NodeType.AGENT,
        confidence=1.0,
        metadata=NodeMetadata(description="specialist agent for billing"),
    )
    return AiSbomDocument(
        target="./app",
        nodes=[src, tgt],
        edges=[Edge(source=src_id, target=tgt_id, relationship_type=EdgeRelationshipType.DELEGATES_TO)],
    )


def test_delegates_to_emits_agent_coverage_scenario():
    sbom = _make_delegation_sbom()
    scenarios = _delegates_to_scenarios(sbom, _make_intent("multi-agent app"))
    assert scenarios, "Expected at least one DELEGATES_TO scenario"
    stype = getattr(scenarios[0].scenario_type, "value", str(scenarios[0].scenario_type))
    assert stype == BehaviorScenarioType.AGENT_COVERAGE.value


def test_delegates_to_targets_downstream_agent():
    sbom = _make_delegation_sbom()
    scenarios = _delegates_to_scenarios(sbom, _make_intent())
    assert any(s.target_component == "TgtAgent" for s in scenarios)


def test_delegates_to_scopes_both_agents():
    sbom = _make_delegation_sbom()
    scenarios = _delegates_to_scenarios(sbom, _make_intent())
    sc = next(s for s in scenarios if s.target_component == "TgtAgent")
    assert "SrcAgent" in sc.scoped_agents
    assert "TgtAgent" in sc.scoped_agents


def test_delegates_to_no_scenarios_without_edge():
    src_id = uuid.uuid5(_NS, "agent/Alone")
    agent = Node(id=src_id, name="Alone", component_type=NodeType.AGENT, confidence=1.0)
    sbom = AiSbomDocument(target="./app", nodes=[agent], edges=[])
    scenarios = _delegates_to_scenarios(sbom, _make_intent())
    assert scenarios == []


# ---------------------------------------------------------------------------
# Endpoint coverage scenario type
# ---------------------------------------------------------------------------


def _make_endpoint_sbom() -> AiSbomDocument:
    eid = uuid.uuid5(_NS, "endpoint//api/chat")
    ep = Node(
        id=eid,
        name="/api/chat",
        component_type=NodeType.API_ENDPOINT,
        confidence=1.0,
        metadata=NodeMetadata(
            description="Chat endpoint",
            accepts_user_input=True,
            chat_payload_key="message",
            request_body_schema={"message": "str", "session_id": "str"},
            returns_sensitive_data=False,
        ),
    )
    return AiSbomDocument(target="./app", nodes=[ep], edges=[])


def test_endpoint_coverage_uses_endpoint_coverage_type():
    """_endpoint_coverage_scenarios must use ENDPOINT_COVERAGE scenario type."""
    sbom = _make_endpoint_sbom()
    scenarios = _endpoint_coverage_scenarios(sbom, _make_intent())
    assert scenarios, "Expected at least one endpoint scenario"
    for sc in scenarios:
        stype = getattr(sc.scenario_type, "value", str(sc.scenario_type))
        assert stype == BehaviorScenarioType.ENDPOINT_COVERAGE.value


def test_endpoint_coverage_target_component_is_endpoint_name():
    sbom = _make_endpoint_sbom()
    scenarios = _endpoint_coverage_scenarios(sbom, _make_intent())
    assert any(s.target_component == "/api/chat" for s in scenarios)


def test_endpoint_coverage_target_component_type_is_api_endpoint():
    sbom = _make_endpoint_sbom()
    scenarios = _endpoint_coverage_scenarios(sbom, _make_intent())
    for sc in scenarios:
        assert sc.target_component_type == "API_ENDPOINT"


# ---------------------------------------------------------------------------
# Policy-topic coverage fallback
# ---------------------------------------------------------------------------


def _make_policy(allowed_topics: list[str]) -> object:
    from nuguard.models.policy import CognitivePolicy
    return CognitivePolicy(allowed_topics=allowed_topics)


def _make_tool_only_sbom() -> AiSbomDocument:
    """SBOM with TOOL nodes but no AGENT nodes — simulates Pinnacle Bank."""
    tid = uuid.uuid5(_NS, "tool/GetBalance")
    return AiSbomDocument(
        target="./app",
        nodes=[Node(id=tid, name="GetBalance", component_type=NodeType.TOOL, confidence=1.0)],
        edges=[],
    )


def test_policy_topic_coverage_emits_one_scenario_per_topic():
    policy = _make_policy(["account inquiries", "fund transfers", "bill payments"])
    sbom = _make_tool_only_sbom()
    scenarios = _policy_topic_coverage_scenarios(_make_intent("banking app"), policy, sbom)  # type: ignore[arg-type]
    assert len(scenarios) == 3


def test_policy_topic_coverage_uses_intent_happy_path_type():
    policy = _make_policy(["account inquiries"])
    sbom = _make_tool_only_sbom()
    scenarios = _policy_topic_coverage_scenarios(_make_intent(), policy, sbom)  # type: ignore[arg-type]
    for sc in scenarios:
        stype = getattr(sc.scenario_type, "value", str(sc.scenario_type))
        assert stype == BehaviorScenarioType.INTENT_HAPPY_PATH.value


def test_policy_topic_coverage_two_turn_messages():
    policy = _make_policy(["fund transfers"])
    sbom = _make_tool_only_sbom()
    scenarios = _policy_topic_coverage_scenarios(_make_intent(), policy, sbom)  # type: ignore[arg-type]
    for sc in scenarios:
        assert len(sc.messages) == 2


def test_policy_topic_coverage_no_scenarios_without_topics():
    policy = _make_policy([])
    sbom = _make_tool_only_sbom()
    scenarios = _policy_topic_coverage_scenarios(_make_intent(), policy, sbom)  # type: ignore[arg-type]
    assert scenarios == []


def test_policy_topic_coverage_capped_at_eight():
    topics = [f"topic_{i}" for i in range(12)]
    policy = _make_policy(topics)
    sbom = _make_tool_only_sbom()
    scenarios = _policy_topic_coverage_scenarios(_make_intent(), policy, sbom)  # type: ignore[arg-type]
    assert len(scenarios) == 8


def test_policy_topic_coverage_not_active_when_agent_nodes_exist():
    """When AGENT nodes are present the fallback must NOT be called from build_scenarios."""
    import asyncio

    from nuguard.behavior.scenarios import build_scenarios
    from nuguard.config import BehaviorConfig

    policy = _make_policy(["account inquiries", "fund transfers"])
    sbom = _make_sbom(["BankingAgent"], ["GetBalance"])  # has an AGENT node

    cfg = BehaviorConfig()
    result = asyncio.run(build_scenarios(cfg, _make_intent("banking app"), policy=policy, sbom=sbom))
    policy_topic_names = [s.name for s in result if s.name.startswith("policy_topic_")]
    assert policy_topic_names == [], (
        "policy_topic_coverage must not activate when AGENT nodes exist"
    )


# ---------------------------------------------------------------------------
# _policy_fragment — trailing-stopword stripping
# ---------------------------------------------------------------------------


def test_policy_fragment_strips_dangling_trailing_preposition():
    # Truncating at max_len=60 lands right after "...assistance for", which
    # would otherwise leave a dangling preposition when embedded in a sentence.
    text = (
        "This app is a multi-agent AI customer service system for airline "
        "reservations and travel assistance for flight status inquiries"
    )
    fragment = _policy_fragment(text, max_len=60)
    assert not fragment.endswith("for")
    assert fragment.split(" ")[-1].lower() not in {"for", "to", "and", "or", "of", "with", "in", "on"}


def test_policy_fragment_untruncated_text_unaffected():
    assert _policy_fragment("cancellation policy") == "cancellation policy"


# ---------------------------------------------------------------------------
# _hitl_probe_scenarios — clean turn 1, marked as warm-up
# ---------------------------------------------------------------------------


def test_hitl_probe_turn1_has_no_duplicated_preposition():
    policy = CognitivePolicy(
        allowed_topics=["travel assistance for flight status inquiries"],
        hitl_triggers=["any cancellation request that changes an existing reservation"],
    )
    intent = _make_intent(
        "This app is a multi-agent AI customer service system for airline "
        "reservations and travel assistance, providing authenticated chat-based "
        "support for tasks such as flight status, cancellations, seat selection, "
        "and baggage inquiries while enforcing strict guardrails and human "
        "escalation policies."
    )
    scenarios = _hitl_probe_scenarios(policy, intent)
    assert scenarios
    turn1 = scenarios[0].messages[0]
    assert "for for" not in turn1
    assert intent.app_purpose not in turn1


def test_hitl_probe_scenario_marks_turn1_as_warmup():
    policy = CognitivePolicy(
        allowed_topics=["account inquiries"],
        hitl_triggers=["any cancellation request"],
    )
    scenarios = _hitl_probe_scenarios(policy, _make_intent())
    assert scenarios
    assert all(s.warmup_turns == 1 for s in scenarios)


# ---------------------------------------------------------------------------
# _generate_data_reactive_turns — no cross-user/IDOR probe
# ---------------------------------------------------------------------------


def test_generate_data_reactive_turns_returns_two_own_account_turns():
    from nuguard.behavior.models import BehaviorScenario
    from nuguard.behavior.models import BehaviorScenarioType as BST
    from nuguard.behavior.runner import _generate_data_reactive_turns

    scenario = BehaviorScenario(
        scenario_type=BST.DATA_DISCOVERY_PROBE,
        name="data_discovery_cancellation_agent",
        messages=["Hi! What bookings do you have on file for me?"],
    )
    intent = _make_intent("airline reservations and travel assistance")
    turns = _generate_data_reactive_turns(
        "Your confirmation number is AA1234 for flight DL-401.", scenario, intent,
    )
    assert len(turns) == 2
    for turn in turns:
        assert "colleague" not in turn.lower()
        assert "another user" not in turn.lower()
        assert "john smith" not in turn.lower()
