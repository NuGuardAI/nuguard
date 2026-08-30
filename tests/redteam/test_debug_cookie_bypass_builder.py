"""Tests for the A09 debug/observability cookie bypass scenario builder."""
from __future__ import annotations

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.scenarios.authorization import build_debug_cookie_bypass


def test_two_steps_baseline_then_cookie_turn() -> None:
    scenario = build_debug_cookie_bypass("agent-1", "Juice Shop Assistant")
    assert scenario.goal_type == GoalType.API_ATTACK
    assert scenario.scenario_type == ScenarioType.DEBUG_COOKIE_BYPASS
    assert len(scenario.chain.steps) == 2

    baseline, cookie_turn = scenario.chain.steps
    assert baseline.extra_headers == {}
    assert baseline.contributes_to_finding is False
    assert baseline.on_failure == "skip"

    assert cookie_turn.extra_headers.get("Cookie", "").count("=true") >= 1
    assert "show_tool_calls" in cookie_turn.extra_headers["Cookie"]
    assert cookie_turn.contributes_to_finding is True
    assert cookie_turn.on_failure == "abort"
    assert cookie_turn.success_requires_new_tool_disclosure is True
    assert cookie_turn.use_llm_eval is False


def test_same_probe_payload_used_for_both_turns() -> None:
    """The only difference between the two turns should be the headers, so
    any disclosure difference is attributable to the toggle, not the wording."""
    scenario = build_debug_cookie_bypass("agent-1", "Assistant")
    baseline, cookie_turn = scenario.chain.steps
    assert baseline.payload == cookie_turn.payload
