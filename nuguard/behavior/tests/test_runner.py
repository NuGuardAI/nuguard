"""Unit tests for nuguard/behavior/runner.py."""
from __future__ import annotations

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


def _make_config() -> MagicMock:
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
    return cfg


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
        scenario_type="invariant_probe",
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


def test_adapt_message_pii_probe_defers_original():
    """When PII is disclosed and not yet probed, probe fires; original is deferred."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(disclosed_pii=["$50,000"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.INTENT_HAPPY_PATH)
    msg, pii, hook, deferred = _adapt_message("Transfer $500", ctx, scenario)

    assert deferred == "Transfer $500"
    assert "\n\n" not in msg          # exactly one question
    assert "$50,000" in msg or "personal" in msg.lower() or "account" in msg.lower()
    assert pii is True
    assert hook is False


def test_adapt_message_skips_probe_for_component_coverage():
    """COMPONENT_COVERAGE bypasses all probes even with PII disclosed."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(disclosed_pii=["$50,000"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.COMPONENT_COVERAGE)
    msg, pii, hook, deferred = _adapt_message("Use get_balance", ctx, scenario)

    assert msg == "Use get_balance"
    assert deferred is None


def test_adapt_message_skips_probe_for_agent_coverage():
    """AGENT_COVERAGE also bypasses all probes."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(follow_up_hooks=["ACCT-001 is active"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.AGENT_COVERAGE)
    msg, pii, hook, deferred = _adapt_message("Route to LoanAdvisor", ctx, scenario)

    assert msg == "Route to LoanAdvisor"
    assert deferred is None


def test_adapt_message_hook_probed_only_once():
    """Hook probe fires on first call; suppressed on subsequent calls."""
    from nuguard.behavior.runner import _adapt_message
    from nuguard.behavior.turn_context import TurnContext

    ctx = TurnContext(follow_up_hooks=["ACCT-001 is active"])
    scenario = _make_adapt_scenario(BehaviorScenarioType.INTENT_HAPPY_PATH)

    # First call — hook fires, original is deferred
    msg1, pii1, hook1, deferred1 = _adapt_message("Next message", ctx, scenario, hook_probed=False)
    assert deferred1 == "Next message"
    assert hook1 is True

    # Second call with hook_probed=True — no probe
    msg2, pii2, hook2, deferred2 = _adapt_message("Another", ctx, scenario, hook_probed=hook1)
    assert msg2 == "Another"
    assert deferred2 is None


def test_adapt_message_no_context_passthrough():
    """With no context, message is returned unchanged."""
    from nuguard.behavior.runner import _adapt_message

    scenario = _make_adapt_scenario()
    msg, pii, hook, deferred = _adapt_message("Hello", None, scenario)
    assert msg == "Hello"
    assert deferred is None
