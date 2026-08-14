"""Unit tests for nuguard/behavior/runner.py."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.behavior.models import (
    BehaviorRunResult,
    BehaviorScenario,
    BehaviorScenarioType,
    IntentProfile,
    ScenarioResult,
)
from nuguard.behavior.runner import BehaviorRunner
from nuguard.common.discovery import DiscoveredProfile
from nuguard.config import BehaviorConfig
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


def _make_intent() -> IntentProfile:
    return IntentProfile(
        app_purpose="Marketing AI assistant",
        core_capabilities=["generate ad copy", "competitor research"],
        behavioral_bounds=["no gambling content"],
    )


def _make_scenario(
    name: str = "test_scenario",
    turns: list[str] | None = None,
    scenario_type: BehaviorScenarioType = BehaviorScenarioType.INTENT_HAPPY_PATH,
) -> BehaviorScenario:
    # Use a name-derived opener so scenarios with different names get distinct opener
    # fingerprints, preventing the opener-dedup in BehaviorRunner from collapsing them.
    return BehaviorScenario(
        scenario_type=scenario_type,
        name=name,
        messages=turns or [f"What can you help me with? (scenario: {name})"],
    )


def _make_config() -> BehaviorConfig:
    cfg = MagicMock()
    cfg.target = "http://localhost:8080"
    cfg.target_url = "http://localhost:8080"
    cfg.target_endpoint = "/chat"
    cfg.chat_payload_key = "message"
    cfg.chat_response_key = "response"
    cfg.chat_payload_list = False
    cfg.chat_payload_format = "json"
    cfg.auth = None
    cfg.max_turns = 6
    cfg.timeout = 30
    cfg.request_timeout = 30.0
    cfg.canary = None
    cfg.session_header = None
    cfg.scenario_delay_seconds = 0.0
    return cfg  # type: ignore[return-value]  # MagicMock duck-types as BehaviorConfig for these tests


def _make_mock_policy() -> MagicMock:
    policy = MagicMock()
    policy.allowed_topics = ["marketing", "advertising"]
    policy.restricted_topics = ["gambling"]
    return policy


def _make_mock_sbom() -> MagicMock:
    sbom = MagicMock()
    sbom.nodes = []
    sbom.edges = []
    return sbom


def _endpoint_node(name: str) -> Node:
    nid = uuid.uuid5(_NS, f"API_ENDPOINT/{name}")
    return Node(
        id=nid,
        name=name,
        component_type=ComponentType.API_ENDPOINT,
        confidence=1.0,
        metadata=NodeMetadata(),
    )


def _make_canned_scenario_result(name: str = "test_scenario", passed: bool = True) -> ScenarioResult:
    return ScenarioResult(
        scenario_id="test-id",
        scenario_name=name,
        scenario_type="intent_happy_path",
        verdicts=[{"overall_score": 4.5, "verdict": "PASS", "agents_mentioned": [], "tools_mentioned": [], "deviations": []}],
        overall_score=4.5,
        coverage_pct=1.0,
        uncovered_agents=[],
        uncovered_tools=[],
        total_turns=1,
        coverage_turns=0,
        deviations=[],
    )


# ---------------------------------------------------------------------------
# BehaviorRunner construction
# ---------------------------------------------------------------------------


def test_runner_construction():
    runner = BehaviorRunner(
        config=_make_config(),
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    assert runner is not None


# ---------------------------------------------------------------------------
# run() with mocked infrastructure via patch.object
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_empty_scenarios():
    runner = BehaviorRunner(
        config=_make_config(),
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    mock_client = AsyncMock()
    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
    ):
        result = await runner.run(scenarios=[])
    assert isinstance(result, BehaviorRunResult)
    assert result.scenario_results == []
    assert result.findings == []


@pytest.mark.asyncio
async def test_run_single_scenario_pass():
    """Happy path: scenario leads to a PASS result."""
    runner = BehaviorRunner(
        config=_make_config(),
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    mock_client = AsyncMock()
    canned_result = _make_canned_scenario_result("test_scenario", passed=True)

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
        patch.object(runner, "_run_scenario", new=AsyncMock(return_value=canned_result)),
    ):
        result = await runner.run(scenarios=[_make_scenario("test_scenario")])

    assert isinstance(result, BehaviorRunResult)
    assert len(result.scenario_results) == 1
    assert result.scenario_results[0].scenario_name == "test_scenario"
    assert result.scenario_results[0].overall_score >= 4.0


@pytest.mark.asyncio
async def test_run_policy_violation_creates_finding():
    """If _run_scenario returns deviations with policy_violation, findings are emitted."""
    runner = BehaviorRunner(
        config=_make_config(),
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    mock_client = AsyncMock()

    violation_result = ScenarioResult(
        scenario_id="test-id",
        scenario_name="boundary_test",
        scenario_type="guardrail_probe",
        verdicts=[{"overall_score": 1.5, "verdict": "FAIL", "agents_mentioned": [], "tools_mentioned": [], "deviations": [{"deviation_type": "policy_violation", "description": "gambling content returned", "severity": "HIGH"}]}],
        overall_score=1.5,
        coverage_pct=0.0,
        uncovered_agents=[],
        uncovered_tools=[],
        total_turns=1,
        coverage_turns=0,
        deviations=[{"deviation_type": "policy_violation", "description": "gambling content returned", "severity": "HIGH"}],
    )

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
        patch.object(runner, "_run_scenario", new=AsyncMock(return_value=violation_result)),
    ):
        result = await runner.run(scenarios=[_make_scenario("boundary_test")])

    assert isinstance(result, BehaviorRunResult)
    assert len(result.findings) > 0
    assert any("policy_violation" in str(f) or "gambling" in str(f) for f in result.findings)


@pytest.mark.asyncio
async def test_run_multiple_scenarios():
    runner = BehaviorRunner(
        config=_make_config(),
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    mock_client = AsyncMock()

    async def _mock_run_scenario(scenario, client, evaluator):
        return _make_canned_scenario_result(scenario.name, passed=True)

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
        patch.object(runner, "_run_scenario", side_effect=_mock_run_scenario),
    ):
        result = await runner.run(
            scenarios=[
                _make_scenario("scenario_a"),
                _make_scenario("scenario_b"),
            ]
        )

    assert len(result.scenario_results) == 2
    names = {r.scenario_name for r in result.scenario_results}
    assert "scenario_a" in names
    assert "scenario_b" in names


@pytest.mark.asyncio
async def test_run_handles_scenario_exception():
    """run() should skip failed scenarios rather than aborting the whole run."""
    runner = BehaviorRunner(
        config=_make_config(),
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    mock_client = AsyncMock()
    call_count = 0

    async def _flaky_run_scenario(scenario, client, evaluator):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Connection refused")
        return _make_canned_scenario_result(scenario.name)

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
        patch.object(runner, "_run_scenario", side_effect=_flaky_run_scenario),
    ):
        result = await runner.run(
            scenarios=[
                _make_scenario("will_fail"),
                _make_scenario("will_pass"),
            ]
        )

    # The second scenario should still have run
    assert len(result.scenario_results) == 1
    assert result.scenario_results[0].scenario_name == "will_pass"


@pytest.mark.asyncio
async def test_run_does_not_rotate_when_target_endpoint_is_explicit() -> None:
    """Explicit target_endpoint must fail fast on 404/405 without SBOM/probe rotation."""

    class _DummyClient:
        def __init__(self) -> None:
            self.base_url = "http://localhost:8080"
            self.chat_path = "/chat"
            self.resolution_notes: list[str] = []
            self.called_paths: list[str] = []

        async def send(self, message: str, session: object) -> tuple[str, list[dict]]:
            self.called_paths.append(self.chat_path)
            if self.chat_path == "/chat":
                return "[HTTP 404] not found", []
            return "OK", []

        def set_chat_endpoint(
            self,
            chat_path: str,
            chat_payload_key: str,
            chat_payload_list: bool,
            chat_response_key: str | None = None,
        ) -> None:
            self.chat_path = chat_path

    cfg = _make_config()
    cfg.target_endpoint = "/chat"

    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            Node(
                id=uuid.uuid5(_NS, "API_ENDPOINT//chat"),
                name="/chat",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.99,
                metadata=NodeMetadata(
                    endpoint="/chat",
                    method="POST",
                    chat_payload_key="message",
                    chat_payload_list=False,
                ),
            ),
            Node(
                id=uuid.uuid5(_NS, "API_ENDPOINT//api/agent/chat"),
                name="/api/agent/chat",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.99,
                metadata=NodeMetadata(
                    endpoint="/api/agent/chat",
                    method="POST",
                    chat_payload_key="message",
                    chat_payload_list=False,
                ),
            ),
        ],
        edges=[],
    )

    runner = BehaviorRunner(
        config=cfg,
        sbom=sbom,
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    dummy = _DummyClient()

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=dummy)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
    ):
        result = await runner.run(
            scenarios=[_make_scenario("explicit_endpoint")],
            pre_scan_profile=DiscoveredProfile(customer_name="Alice", ids=["ACCT-001"]),
        )

    assert result.scan_outcome == "aborted_endpoint_unreachable"
    assert any("Explicit endpoint precedence is enforced" in note for note in result.config_notes)
    assert "/api/agent/chat" not in dummy.called_paths


def test_build_coverage_map_endpoint_direct_match_confidence() -> None:
    sbom = AiSbomDocument(target="./app", nodes=[_endpoint_node("/api/agent/chat")], edges=[])
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    scenario_results = [
        ScenarioResult(
            scenario_id="s1",
            scenario_name="endpoint_s1",
            scenario_type=BehaviorScenarioType.ENDPOINT_COVERAGE.value,
            verdicts=[
                {
                    "turn": 1,
                    "target_component": "/api/agent/chat",
                    "effective_endpoint": "/api/agent/chat?session=1",
                    "deviations": [],
                    "passed": True,
                }
            ],
            total_turns=1,
        )
    ]

    coverage = runner._build_coverage_map(scenario_results)
    endpoint_cov = next(c for c in coverage if c.component_name == "/api/agent/chat")
    assert endpoint_cov.exercised is True
    assert endpoint_cov.mapping_confidence == "direct_match"
    assert endpoint_cov.mapped_from_endpoint == "/api/agent/chat"


def test_build_coverage_map_endpoint_normalized_match_confidence() -> None:
    sbom = AiSbomDocument(target="./app", nodes=[_endpoint_node("/api/agent/chat")], edges=[])
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    scenario_results = [
        ScenarioResult(
            scenario_id="s1",
            scenario_name="endpoint_s1",
            scenario_type=BehaviorScenarioType.ENDPOINT_COVERAGE.value,
            verdicts=[
                {
                    "turn": 1,
                    "target_component": "/api/unknown",
                    "effective_endpoint": "http://localhost:8080/api/agent/chat/",
                    "deviations": [],
                    "passed": True,
                }
            ],
            total_turns=1,
        )
    ]

    coverage = runner._build_coverage_map(scenario_results)
    endpoint_cov = next(c for c in coverage if c.component_name == "/api/agent/chat")
    assert endpoint_cov.exercised is True
    assert endpoint_cov.mapping_confidence == "normalized_match"
    assert endpoint_cov.mapped_from_endpoint == "/api/agent/chat"


def test_build_coverage_map_runtime_only_endpoint_fallback() -> None:
    sbom = AiSbomDocument(target="./app", nodes=[_endpoint_node("/api/agent/chat")], edges=[])
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    scenario_results = [
        ScenarioResult(
            scenario_id="s1",
            scenario_name="endpoint_s1",
            scenario_type=BehaviorScenarioType.ENDPOINT_COVERAGE.value,
            verdicts=[
                {
                    "turn": 1,
                    "target_component": "/api/not-in-sbom",
                    "effective_endpoint": "/api/runtime-only",
                    "deviations": [],
                    "passed": True,
                }
            ],
            total_turns=1,
        )
    ]

    coverage = runner._build_coverage_map(scenario_results)
    runtime_cov = next(c for c in coverage if c.component_name == "/api/runtime-only (runtime)")
    assert runtime_cov.exercised is True
    assert runtime_cov.mapping_confidence == "runtime_only_unmapped"
    assert runtime_cov.mapped_from_endpoint == "/api/runtime-only"
    assert runner._coverage_mapping_diagnostics.get("runtime_only_unmapped_endpoint_count") == 1


# ---------------------------------------------------------------------------
# Agent/tool mention matching tiers (descriptive_name, fuzzy, sole-agent
# fallback, config aliases) — reduces false "unmatched component" reports.
# ---------------------------------------------------------------------------


def _agent_or_tool_node(
    name: str,
    component_type: ComponentType,
    descriptive_name: str | None = None,
) -> Node:
    nid = uuid.uuid5(_NS, f"{component_type.value}/{name}")
    return Node(
        id=nid,
        name=name,
        component_type=component_type,
        confidence=1.0,
        metadata=NodeMetadata(descriptive_name=descriptive_name),
    )


def _mention_scenario_result(agents: list[str], tools: list[str]) -> ScenarioResult:
    return ScenarioResult(
        scenario_id="s1",
        scenario_name="mention_s1",
        scenario_type=BehaviorScenarioType.INTENT_HAPPY_PATH.value,
        verdicts=[
            {
                "turn": 1,
                "agents_mentioned": agents,
                "tools_mentioned": tools,
                "deviations": [],
            }
        ],
        total_turns=1,
    )


def test_build_coverage_map_descriptive_name_match():
    """A tool mentioned by its SBOM metadata.descriptive_name (not its `name`) matches."""
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_agent_or_tool_node("Check Sanctions", ComponentType.TOOL, descriptive_name="Sanctions Screening Tool")],
        edges=[],
    )
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map([_mention_scenario_result(agents=[], tools=["Sanctions Screening Tool"])])
    cov = next(c for c in coverage if c.component_name == "Check Sanctions")
    assert cov.exercised is True
    assert cov.mapping_confidence == "descriptive_name_match"
    assert "Sanctions Screening Tool" in cov.evidence_mentions
    assert runner._coverage_mapping_diagnostics.get("descriptive_name_match_count") == 1
    assert runner._coverage_mapping_diagnostics.get("mentioned_entities_unmapped") == []


def test_build_coverage_map_call_style_prefix_normalises():
    """A function-call-style mention (`functions.cancel_payment`) still matches via normalisation."""
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_agent_or_tool_node("Cancel Payment", ComponentType.TOOL)],
        edges=[],
    )
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map([_mention_scenario_result(agents=[], tools=["functions.cancel_payment"])])
    cov = next(c for c in coverage if c.component_name == "Cancel Payment")
    assert cov.exercised is True
    assert cov.mapping_confidence == "normalized_match"


def test_build_coverage_map_sole_agent_fallback_resolves_persona_name():
    """A live app's self-chosen persona name (unrelated to the SBOM name) resolves
    to the sole AGENT node — this is the exact 'Nova' vs 'Fintech App Assistant' case."""
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_agent_or_tool_node("Fintech App Assistant", ComponentType.AGENT)],
        edges=[],
    )
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map(
        [_mention_scenario_result(agents=["Nova (AI Banking Assistant)"], tools=[])]
    )
    cov = next(c for c in coverage if c.component_name == "Fintech App Assistant")
    assert cov.exercised is True
    assert cov.mapping_confidence == "sole_agent_fallback"
    assert runner._coverage_mapping_diagnostics.get("sole_agent_fallback_count") == 1


def test_build_coverage_map_sole_agent_fallback_disabled_when_multiple_agents():
    """The fallback must not fire when the SBOM has 2+ agents — attribution would be a guess."""
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            _agent_or_tool_node("Fintech App Assistant", ComponentType.AGENT),
            _agent_or_tool_node("Wealth Advisor Agent", ComponentType.AGENT),
        ],
        edges=[],
    )
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map(
        [_mention_scenario_result(agents=["Nova (AI Banking Assistant)"], tools=[])]
    )
    assert all(not c.exercised for c in coverage)
    assert "Nova (AI Banking Assistant)" in runner._coverage_mapping_diagnostics.get("mentioned_entities_unmapped", [])


def test_build_coverage_map_sole_agent_fallback_respects_config_opt_out():
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_agent_or_tool_node("Fintech App Assistant", ComponentType.AGENT)],
        edges=[],
    )
    cfg = _make_config()
    cfg.sole_agent_alias_fallback = False
    runner = BehaviorRunner(config=cfg, sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map(
        [_mention_scenario_result(agents=["Nova (AI Banking Assistant)"], tools=[])]
    )
    cov = next(c for c in coverage if c.component_name == "Fintech App Assistant")
    assert cov.exercised is False
    assert "Nova (AI Banking Assistant)" in runner._coverage_mapping_diagnostics.get("mentioned_entities_unmapped", [])


def test_build_coverage_map_fuzzy_match_typo():
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_agent_or_tool_node("Cancel Payment", ComponentType.TOOL)],
        edges=[],
    )
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map([_mention_scenario_result(agents=[], tools=["Cancel Paymant"])])
    cov = next(c for c in coverage if c.component_name == "Cancel Payment")
    assert cov.exercised is True
    assert cov.mapping_confidence == "fuzzy_match"


def test_build_coverage_map_fuzzy_match_ambiguous_tie_stays_unmatched():
    """Two similarly-named tools scoring within epsilon of each other must not be
    guessed between — safer to leave unmatched than pick an arbitrary winner."""
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            _agent_or_tool_node("Xylophane Tool", ComponentType.TOOL),
            _agent_or_tool_node("Xylaphone Tool", ComponentType.TOOL),
        ],
        edges=[],
    )
    runner = BehaviorRunner(config=_make_config(), sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map([_mention_scenario_result(agents=[], tools=["Xylophone Tool"])])
    assert all(not c.exercised for c in coverage)
    assert "Xylophone Tool" in runner._coverage_mapping_diagnostics.get("mentioned_entities_unmapped", [])


def test_build_coverage_map_config_component_alias_takes_priority():
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_agent_or_tool_node("Fintech App Assistant", ComponentType.AGENT)],
        edges=[],
    )
    cfg = _make_config()
    cfg.component_aliases = {"Nova": "Fintech App Assistant"}
    runner = BehaviorRunner(config=cfg, sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map([_mention_scenario_result(agents=["Nova"], tools=[])])
    cov = next(c for c in coverage if c.component_name == "Fintech App Assistant")
    assert cov.exercised is True
    assert cov.mapping_confidence == "config_alias"
    assert runner._coverage_mapping_diagnostics.get("config_alias_match_count") == 1


def test_build_coverage_map_genuinely_unmatched_mention_stays_unmatched():
    """A mention with no plausible SBOM counterpart must remain unmatched (no false positives)."""
    sbom = AiSbomDocument(
        target="./app",
        nodes=[_agent_or_tool_node("Fintech App Assistant", ComponentType.AGENT)],
        edges=[],
    )
    cfg = _make_config()
    cfg.sole_agent_alias_fallback = False
    runner = BehaviorRunner(config=cfg, sbom=sbom, policy=None, intent=_make_intent(), llm_client=None)

    coverage = runner._build_coverage_map([_mention_scenario_result(agents=["ComplianceOfficer"], tools=[])])
    assert all(not c.exercised for c in coverage)
    assert "ComplianceOfficer" in runner._coverage_mapping_diagnostics.get("mentioned_entities_unmapped", [])


# ---------------------------------------------------------------------------
# v7.5: _adapt_message — probe deferral + scenario-type filter
# ---------------------------------------------------------------------------


def _make_adapt_scenario(
    scenario_type: BehaviorScenarioType = BehaviorScenarioType.INTENT_HAPPY_PATH,
) -> BehaviorScenario:
    return BehaviorScenario(
        scenario_type=scenario_type,
        name="adapt_test",
        messages=["hello"],
    )


async def test_adapt_message_pii_probe_defers_original():
    """When PII is disclosed and not yet probed, probe fires; original is deferred."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(disclosed_pii=["$50,000"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.INTENT_HAPPY_PATH)
    msg, pii, hook, deferred = await _adapt_message("Transfer $500", ctx, scenario)

    assert deferred == "Transfer $500"
    assert "\n\n" not in msg          # exactly one question
    assert "$50,000" in msg or "personal" in msg.lower() or "account" in msg.lower()
    assert pii is True
    assert hook is False


async def test_adapt_message_skips_probe_for_component_coverage():
    """COMPONENT_COVERAGE bypasses all probes even with PII disclosed."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(disclosed_pii=["$50,000"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.COMPONENT_COVERAGE)
    msg, pii, hook, deferred = await _adapt_message("Use get_balance", ctx, scenario)

    assert msg == "Use get_balance"
    assert deferred is None


async def test_adapt_message_skips_probe_for_agent_coverage():
    """AGENT_COVERAGE also bypasses all probes."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(follow_up_hooks=["ACCT-001 is active"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.AGENT_COVERAGE)
    msg, pii, hook, deferred = await _adapt_message("Route to LoanAdvisor", ctx, scenario)

    assert msg == "Route to LoanAdvisor"
    assert deferred is None


async def test_adapt_message_hook_probed_only_once():
    """Hook probe fires on first call; suppressed on subsequent calls."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(follow_up_hooks=["ACCT-001 is active"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.INTENT_HAPPY_PATH)

    # First call — hook fires, original is deferred
    msg1, pii1, hook1, deferred1 = await _adapt_message("Next message", ctx, scenario, hook_probed=False)
    assert deferred1 == "Next message"
    assert hook1 is True

    # Second call with hook_probed=True — no probe
    msg2, pii2, hook2, deferred2 = await _adapt_message("Another", ctx, scenario, hook_probed=hook1)
    assert msg2 == "Another"
    assert deferred2 is None


async def test_adapt_message_no_context_passthrough():
    """With no context, message is returned unchanged."""
    from nuguard.behavior.runner import _adapt_message

    scenario = _make_adapt_scenario()
    msg, pii, hook, deferred = await _adapt_message("Hello", None, scenario)
    assert msg == "Hello"
    assert deferred is None


# ---------------------------------------------------------------------------
# Configurable coverage/session turn budgets
# ---------------------------------------------------------------------------


def test_adaptive_coverage_cap_uses_config_value():
    from nuguard.behavior.runner import _adaptive_coverage_cap

    config = BehaviorConfig(coverage_turns_per_scenario=8)
    assert _adaptive_coverage_cap(config) == 8


def test_adaptive_coverage_cap_falls_back_to_default():
    from nuguard.behavior.coverage import MAX_COVERAGE_TURNS
    from nuguard.behavior.runner import _adaptive_coverage_cap

    assert _adaptive_coverage_cap(object()) == MAX_COVERAGE_TURNS


def test_session_turn_cap_uses_config_value():
    from nuguard.behavior.runner import _session_turn_cap

    config = BehaviorConfig(max_session_turns=25)
    assert _session_turn_cap(config) == 25


def test_session_turn_cap_falls_back_to_default():
    from nuguard.behavior.runner import _ADAPTIVE_SESSION_CAP, _session_turn_cap

    assert _session_turn_cap(object()) == _ADAPTIVE_SESSION_CAP


# ---------------------------------------------------------------------------
# GUIDED_COVERAGE scenarios: driven turn-by-turn by CoverageDirector
# ---------------------------------------------------------------------------


def _make_sbom_with_tool(tool_name: str) -> AiSbomDocument:
    return AiSbomDocument(
        target="test",
        nodes=[
            Node(
                name=tool_name,
                component_type=ComponentType.TOOL,
                confidence=1.0,
                metadata=NodeMetadata(),
            )
        ],
    )


@pytest.mark.asyncio
async def test_guided_coverage_scenario_uses_coverage_director_not_batch_gen():
    """GUIDED_COVERAGE scenarios must be driven one message at a time via
    CoverageDirector.next_message(), never via the batch generate_coverage_turns()."""
    runner = BehaviorRunner(
        config=BehaviorConfig(target="http://localhost:8080", target_endpoint="/chat"),
        sbom=_make_sbom_with_tool("transfer_funds"),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    runner._pre_scan_profile = None

    mock_client = AsyncMock()
    mock_client.base_url = "http://localhost:8080"
    mock_client.send = AsyncMock(return_value=("Sure, I can help with that.", []))

    fake_director = MagicMock()
    fake_director.next_message = AsyncMock(side_effect=["Can you move $50 to savings?", None])

    scenario = BehaviorScenario(
        scenario_type=BehaviorScenarioType.GUIDED_COVERAGE,
        name="guided_assistant_0",
        messages=["I need help with banking. Can you help me get started?"],
        scoped_tools=["transfer_funds"],
        primary_agent="assistant",
    )

    with (
        patch.object(runner, "_coverage_director", return_value=fake_director),
        patch("nuguard.behavior.runner.generate_coverage_turns", new=AsyncMock()) as batch_gen,
    ):
        result = await runner._run_scenario(scenario, mock_client, None)

    assert result.scenario_name == "guided_assistant_0"
    # Opening scripted message + one director-picked follow-up = 2 turns, then
    # the director returning None ends the scenario.
    assert mock_client.send.await_count == 2
    fake_director.next_message.assert_awaited()
    batch_gen.assert_not_awaited()
