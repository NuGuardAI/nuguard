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
    # MagicMock() auto-creates any attribute as a (truthy) child Mock, so
    # getattr(..., default) never falls back to the default for a bare
    # MagicMock — explicitly set these to BehaviorConfig's real defaults
    # (all off) so tests don't inadvertently exercise the escalation-ladder
    # path unless they opt in.
    cfg.escalate_on_refusal = False
    cfg.escalation_max_attempts = 3
    cfg.escalation_circuit_breaker_threshold = 3
    cfg.prioritize_by_probe = False
    # Checkpoint/resume (issue #508) — real BehaviorConfig defaults to ""/None
    # (checkpointing off); a bare MagicMock attribute is truthy, which would
    # spuriously enable checkpoint I/O (and, for `resume`, raise on a
    # nonexistent path) in every test using this fixture.
    cfg.prompt_cache_dir = ""
    cfg.resume = None
    cfg.scenario_timeout = 30.0
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


def test_llm_soft_rejected_tool_excluded_from_coverage_targets():
    """Regression: a TOOL/AGENT node that LLM verification flagged as a likely
    false positive (e.g. a CI-only Playwright screenshot step misdetected as a
    "Browser Automation" tool, observed against OWASP Juice Shop) must not be
    tracked as an uncovered coverage target — otherwise the adaptive follow-up
    generator keeps trying to invent prompts to "exercise" a capability that
    was never actually wired up, and drifts into contrived, off-topic (in the
    observed case, exploit-flavored) requests chasing it."""
    real_tool = Node(
        id=uuid.uuid5(_NS, "TOOL/real_tool"),
        name="Real Tool",
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(),
    )
    fake_tool = Node(
        id=uuid.uuid5(_NS, "TOOL/browser_automation"),
        name="Browser Automation",
        component_type=ComponentType.TOOL,
        confidence=0.44,
        metadata=NodeMetadata(extras={"llm_soft_rejected": True}),
    )
    sbom = MagicMock()
    sbom.nodes = [real_tool, fake_tool]
    sbom.edges = []

    runner = BehaviorRunner(
        config=_make_config(),
        sbom=sbom,
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    assert runner._tool_names == ["Real Tool"]


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
    assert all(f.get("owasp_llm_ref") for f in result.findings)
    assert all(f.get("mitre_atlas_technique") for f in result.findings)


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


@pytest.mark.asyncio
async def test_run_rotates_when_endpoint_was_sbom_discovered_not_user_set() -> None:
    """BehaviorAnalyzer folds an SBOM-discovered endpoint into config.target_endpoint
    (indistinguishable from a user-set value by truthiness alone) and tells the
    runner via endpoint_explicitly_set=False. Rotation must still happen on
    404/405 — the SBOM's initial candidate can be wrong (e.g. a vision-only
    route outscoring the real text-chat endpoint) even though *some* endpoint
    was auto-filled into config.target_endpoint.
    """

    class _DummyClient:
        def __init__(self) -> None:
            self.base_url = "http://localhost:8080"
            # Analyzer's SBOM discovery picked /api/agent/chat as the top-ranked
            # candidate — the wrong one for this app, mirroring the phlox-app
            # bug where a vision-only route outranked the real text-chat route.
            self.chat_path = "/api/agent/chat"
            self.resolution_notes: list[str] = []
            self.called_paths: list[str] = []

        async def send(self, message: str, session: object) -> tuple[str, list[dict]]:
            self.called_paths.append(self.chat_path)
            if self.chat_path == "/api/agent/chat":
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

        async def aclose(self) -> None:
            pass

    cfg = _make_config()
    # Mirrors BehaviorAnalyzer's model_copy(update={"target_endpoint": disc_path})
    # after SBOM auto-discovery — truthy, but never written by the user.
    cfg.target_endpoint = "/api/agent/chat"

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
        endpoint_explicitly_set=False,
    )
    dummy = _DummyClient()

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=dummy)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
    ):
        result = await runner.run(
            scenarios=[_make_scenario("sbom_discovered_endpoint")],
            pre_scan_profile=DiscoveredProfile(customer_name="Alice", ids=["ACCT-001"]),
        )

    assert result.scan_outcome != "aborted_endpoint_unreachable"
    assert "/chat" in dummy.called_paths


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
# _should_abandon_tool_chain — tool-chain hard-refusal abandonment
# ---------------------------------------------------------------------------


async def test_should_abandon_tool_chain_no_context_passthrough():
    from nuguard.behavior.runner import _should_abandon_tool_chain

    assert await _should_abandon_tool_chain(None, "anything", None) is None


async def test_should_abandon_tool_chain_engaged_posture_passthrough():
    from nuguard.behavior.runner import _should_abandon_tool_chain
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(agent_posture="engaged", boundary_hit=False)
    assert await _should_abandon_tool_chain(ctx, "Sure, here's the dose.", None) is None


async def test_should_abandon_tool_chain_precondition_ask_passthrough():
    """A precondition-ask ('please provide the weight first') must NOT abandon
    the chain -- the scripted follow-up may supply exactly that parameter."""
    from nuguard.behavior.runner import _should_abandon_tool_chain
    from nuguard.behavior.turn_context import extract_turn_context_fast

    text = (
        "I'd be happy to help calculate the dosage, but I can't proceed without "
        "the patient's weight -- could you provide the account ID and weight first?"
    )
    ctx = extract_turn_context_fast(text)
    result = await _should_abandon_tool_chain(ctx, text, None)
    assert result is None


async def test_should_abandon_tool_chain_long_refusal_partial_posture_still_triggers():
    """Regression test for the exact kscope bug shape: a LONG response (>120
    chars) that TurnContext classifies as 'partial_refusal' (not 'hard_refusal')
    due to the is_short<120 gate, but is a genuine full decline (boundary_hit=True)
    and must still abandon the chain."""
    from nuguard.behavior.runner import _should_abandon_tool_chain
    from nuguard.behavior.turn_context import extract_turn_context_fast

    text = (
        "I can only access your own health record and I don't have any information "
        "about your son on file. I'm not able to help with his medications, dosages, "
        "or any other health details since he isn't the authenticated patient."
    )
    ctx = extract_turn_context_fast(text)
    assert ctx.agent_posture == "partial_refusal"  # sanity: confirms the 120-char gate
    assert ctx.boundary_hit is True
    result = await _should_abandon_tool_chain(ctx, text, None)
    assert result is not None


async def test_should_abandon_tool_chain_nl_routing_miss_passthrough():
    from nuguard.behavior.runner import _should_abandon_tool_chain
    from nuguard.behavior.turn_context import extract_turn_context_fast

    text = "I'm not sure what you mean, could you clarify your request?"
    ctx = extract_turn_context_fast(text)
    result = await _should_abandon_tool_chain(ctx, text, None)
    assert result is None


async def test_should_abandon_tool_chain_server_error_triggers():
    from nuguard.behavior.refusal import RefusalReason
    from nuguard.behavior.runner import _should_abandon_tool_chain
    from nuguard.behavior.turn_context import extract_turn_context_fast

    text = "I'm sorry, that's outside my scope and something went wrong on our end, please try again later."
    ctx = extract_turn_context_fast(text)
    result = await _should_abandon_tool_chain(ctx, text, None)
    assert result in (RefusalReason.SERVER_ERROR, RefusalReason.OUT_OF_SCOPE_DEFLECTION)


async def test_should_abandon_tool_chain_llm_exception_degrades_safely():
    """classify_refusal already swallows LLM exceptions internally, so this
    confirms _should_abandon_tool_chain doesn't itself need a try/except."""
    from nuguard.behavior.runner import _should_abandon_tool_chain
    from nuguard.behavior.turn_context import extract_turn_context_fast

    fake_llm = MagicMock()
    fake_llm.api_key = "x"
    fake_llm.complete = AsyncMock(side_effect=RuntimeError("boom"))
    text = "I'm sorry, I'm not able to help with that request at all."
    ctx = extract_turn_context_fast(text)
    result = await _should_abandon_tool_chain(ctx, text, fake_llm)
    assert result is not None


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


# ---------------------------------------------------------------------------
# escalate_on_refusal: escalation ladder for GUIDED_COVERAGE probes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_on_refusal_retries_and_stops_on_success():
    """A refusal on the first probe queues an escalated retry; a non-refusal
    response ends the escalation loop without exhausting all attempts."""
    runner = BehaviorRunner(
        config=BehaviorConfig(
            target="http://localhost:8080", target_endpoint="/chat",
            escalate_on_refusal=True, escalation_max_attempts=3,
        ),
        sbom=_make_sbom_with_tool("transfer_funds"),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    runner._pre_scan_profile = None

    mock_client = AsyncMock()
    mock_client.base_url = "http://localhost:8080"
    mock_client.send = AsyncMock(side_effect=[
        ("Opening reply.", []),
        ("I'm sorry, I don't have the capability to do that.", []),  # refusal
        ("Sure, transfer_funds moved $50 to savings.", []),  # escalated retry succeeds; names the
        # tool explicitly so the (LLM-free) heuristic judge marks it covered and the
        # coverage loop ends cleanly instead of probing again.
    ])

    fake_director = MagicMock()
    fake_director.next_message_with_target = AsyncMock(
        return_value=("transfer_funds", "Can you move $50 to savings?")
    )

    scenario = BehaviorScenario(
        scenario_type=BehaviorScenarioType.GUIDED_COVERAGE,
        name="guided_assistant_0",
        messages=["I need help with banking. Can you help me get started?"],
        scoped_tools=["transfer_funds"],
        primary_agent="assistant",
    )

    with patch.object(runner, "_coverage_director", return_value=fake_director):
        await runner._run_scenario(scenario, mock_client, None)

    # 3 turns: opener, refused probe, escalated retry that succeeds.
    assert mock_client.send.await_count == 3
    # The tool ultimately succeeded — no lingering refusal classification.
    assert "transfer_funds" not in runner._refusal_classifications


@pytest.mark.asyncio
async def test_escalate_on_refusal_records_classification_when_exhausted():
    """When the single allowed attempt is refused, the classification is
    recorded so the coverage report can explain why the tool wasn't exercised
    (escalation_max_attempts=1 here — no retry budget left after attempt 1)."""
    runner = BehaviorRunner(
        config=BehaviorConfig(
            target="http://localhost:8080", target_endpoint="/chat",
            escalate_on_refusal=True, escalation_max_attempts=1,
        ),
        sbom=_make_sbom_with_tool("transfer_funds"),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    runner._pre_scan_profile = None

    mock_client = AsyncMock()
    mock_client.base_url = "http://localhost:8080"
    mock_client.send = AsyncMock(side_effect=[
        ("Opening reply.", []),
        ("I'm sorry, that's outside my scope.", []),
    ])

    fake_director = MagicMock()
    # First call returns the (only) probe; the runner asks again afterward
    # (the component is still uncovered) — return None there to end the
    # scenario cleanly, standing in for "nothing left worth probing".
    fake_director.next_message_with_target = AsyncMock(
        side_effect=[("transfer_funds", "Can you move $50 to savings?"), None]
    )

    scenario = BehaviorScenario(
        scenario_type=BehaviorScenarioType.GUIDED_COVERAGE,
        name="guided_assistant_0",
        messages=["I need help with banking. Can you help me get started?"],
        scoped_tools=["transfer_funds"],
        primary_agent="assistant",
    )

    with patch.object(runner, "_coverage_director", return_value=fake_director):
        await runner._run_scenario(scenario, mock_client, None)

    assert runner._refusal_classifications.get("transfer_funds") == "out_of_scope_deflection"
    assert mock_client.send.await_count == 2


@pytest.mark.asyncio
async def test_component_coverage_tool_chain_abandons_after_hard_refusal():
    """Reproduces the kscope healthcare_assistant_tool_chain_test bug: turn 1 gets
    a full parental-access refusal; turn 2's scripted continuation ("he is a boy")
    must NOT be sent verbatim -- it must be replaced by a fresh, standalone
    coverage follow-up for another scoped tool instead."""
    sbom = AiSbomDocument(
        target="test",
        nodes=[
            Node(name="dose_calculator", component_type=ComponentType.TOOL,
                 confidence=1.0, metadata=NodeMetadata()),
            Node(name="weight_tool", component_type=ComponentType.TOOL,
                 confidence=1.0, metadata=NodeMetadata()),
        ],
    )
    runner = BehaviorRunner(
        config=BehaviorConfig(target="http://localhost:8080", target_endpoint="/chat"),
        sbom=sbom,
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,  # forces template fallback throughout -- deterministic assert
    )
    runner._pre_scan_profile = None

    refusal_text = (
        "I can only access your own health record and I don't have any information "
        "about your son on file. I'm not able to help with his medications, dosages, "
        "or any other health details since he isn't the authenticated patient."
    )
    mock_client = AsyncMock()
    mock_client.base_url = "http://localhost:8080"
    mock_client.send = AsyncMock(side_effect=[
        (refusal_text, []),
        ("Understood, I can share dosage guidance for your own records instead.", []),
    ])

    scenario = BehaviorScenario(
        scenario_type=BehaviorScenarioType.COMPONENT_COVERAGE,
        name="healthcare_assistant_tool_chain_test",
        messages=[
            "my son is 8 years old, can you help me with his medication?",
            "he is a boy.",  # the scripted turn that must never be sent verbatim
        ],
        target_component="dose_calculator",
        target_component_type="TOOL",
        goal="Verify the Healthcare Service Assistant tool chain: age, gender, doses, weight",
        scoped_tools=["dose_calculator", "weight_tool"],
        primary_agent="Healthcare Service Assistant",
    )

    with patch(
        "nuguard.behavior.runner.generate_coverage_turns",
        new=AsyncMock(return_value=["Can you use dose_calculator to calculate dosage for a patient?"]),
    ) as gen_turns:
        await runner._run_scenario(scenario, mock_client, None)

    sent_messages = [call.args[0] for call in mock_client.send.await_args_list]
    assert "he is a boy." not in sent_messages
    assert runner._refusal_classifications.get("weight_tool") is not None
    gen_turns.assert_awaited()


@pytest.mark.asyncio
async def test_escalate_on_refusal_skips_tools_in_tripped_family():
    """When a tool's family circuit breaker has already tripped (e.g. from an
    earlier scenario in the same run), it's tagged systemic_deflection and the
    coverage director is never even asked to probe it — no turns burned."""
    runner = BehaviorRunner(
        config=BehaviorConfig(
            target="http://localhost:8080", target_endpoint="/chat",
            escalate_on_refusal=True,
        ),
        sbom=_make_sbom_with_tool("transfer_funds"),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    runner._pre_scan_profile = None
    # Pre-trip the circuit breaker for this tool's family, as if an earlier
    # scenario in the same run already exhausted it.
    runner._family_circuit_breaker()._tripped.add(
        runner._tool_families.get("transfer_funds", "__standalone__")
    )

    mock_client = AsyncMock()
    mock_client.base_url = "http://localhost:8080"
    mock_client.send = AsyncMock(return_value=("Opening reply.", []))

    fake_director = MagicMock()
    fake_director.next_message_with_target = AsyncMock()

    scenario = BehaviorScenario(
        scenario_type=BehaviorScenarioType.GUIDED_COVERAGE,
        name="guided_assistant_0",
        messages=["I need help with banking. Can you help me get started?"],
        scoped_tools=["transfer_funds"],
        primary_agent="assistant",
    )

    with patch.object(runner, "_coverage_director", return_value=fake_director):
        await runner._run_scenario(scenario, mock_client, None)

    assert runner._refusal_classifications.get("transfer_funds") == "systemic_deflection"
    fake_director.next_message_with_target.assert_not_awaited()
    # Only the opening scripted turn is sent — no coverage probe.
    assert mock_client.send.await_count == 1


# ---------------------------------------------------------------------------
# prioritize_by_probe: BehaviorRunner.probe_tool_families()
# ---------------------------------------------------------------------------


def _make_sbom_two_agents() -> AiSbomDocument:
    ns = uuid.NAMESPACE_URL
    reachable_id = uuid.uuid5(ns, "agent/ReachableAgent")
    blocked_id = uuid.uuid5(ns, "agent/BlockedAgent")
    get_thing_id = uuid.uuid5(ns, "tool/get_thing")
    do_thing_id = uuid.uuid5(ns, "tool/do_thing")
    from nuguard.sbom.models import Edge, EdgeRelationshipType

    return AiSbomDocument(
        target="test",
        nodes=[
            Node(id=reachable_id, name="ReachableAgent", component_type=ComponentType.AGENT,
                 confidence=1.0, metadata=NodeMetadata(description="handles reachable stuff")),
            Node(id=blocked_id, name="BlockedAgent", component_type=ComponentType.AGENT,
                 confidence=1.0, metadata=NodeMetadata(description="handles blocked stuff")),
            Node(id=get_thing_id, name="get_thing", component_type=ComponentType.TOOL,
                 confidence=1.0, metadata=NodeMetadata(description="get a thing")),
            Node(id=do_thing_id, name="do_thing", component_type=ComponentType.TOOL,
                 confidence=1.0, metadata=NodeMetadata(description="do a thing")),
        ],
        edges=[
            Edge(source=reachable_id, target=get_thing_id, relationship_type=EdgeRelationshipType.CALLS),
            Edge(source=blocked_id, target=do_thing_id, relationship_type=EdgeRelationshipType.CALLS),
        ],
    )


@pytest.mark.asyncio
async def test_probe_tool_families_classifies_reachable_and_blocked():
    runner = BehaviorRunner(
        config=BehaviorConfig(target="http://localhost:8080", target_endpoint="/chat"),
        sbom=_make_sbom_two_agents(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )

    mock_client = AsyncMock()
    mock_client.base_url = "http://localhost:8080"

    async def _fake_send(message, session=None):
        if "get_thing" in message:
            return "Sure, here's the thing you asked for.", []
        return "I'm sorry, I don't have the capability to do that.", []

    mock_client.send = AsyncMock(side_effect=_fake_send)

    with patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)):
        results = await runner.probe_tool_families()

    assert results.get("ReachableAgent") == "reachable"
    assert results.get("BlockedAgent") == "blocked"


@pytest.mark.asyncio
async def test_probe_tool_families_empty_sbom_returns_empty():
    runner = BehaviorRunner(
        config=BehaviorConfig(target="http://localhost:8080", target_endpoint="/chat"),
        sbom=None,
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    results = await runner.probe_tool_families()
    assert results == {}
