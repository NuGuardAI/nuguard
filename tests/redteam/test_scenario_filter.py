"""Coverage for the redteam.scenarios goal-type filter (nuguard.yaml `scenarios:`).

Regression guard for a real bug: the documented token "prompt-injection"
silently matched zero scenarios because GoalType.PROMPT_DRIVEN_THREAT was
renamed at some point and the docs/example were never updated — any config
using that token dropped the entire PROMPT_DRIVEN_THREAT category (the
largest attack family) without any error or warning. This asserts every
canonical token for all 9 GoalTypes actually matches, plus the back-compat
alias for the old broken token.
"""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.executor.orchestrator import (
    _normalize_scenario_token,
    _scenario_matches_filter,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario

# One representative ScenarioType per GoalType, paired with the canonical
# config token users should be able to select that GoalType with.
_GOAL_TYPE_CASES: list[tuple[GoalType, ScenarioType, str]] = [
    (GoalType.PROMPT_DRIVEN_THREAT, ScenarioType.SYSTEM_PROMPT_EXTRACTION, "prompt-driven-threat"),
    (GoalType.POLICY_VIOLATION, ScenarioType.RESTRICTED_ACTION, "policy-violation"),
    (GoalType.DATA_EXFILTRATION, ScenarioType.DIRECT_PII_EXTRACTION, "data-exfiltration"),
    (GoalType.PRIVILEGE_ESCALATION, ScenarioType.PRIVILEGE_CHAIN, "privilege-escalation"),
    (GoalType.TOOL_ABUSE, ScenarioType.SQL_INJECTION, "tool-abuse"),
    (GoalType.MCP_TOXIC_FLOW, ScenarioType.MCP_TOOL_INJECTION, "mcp-toxic-flow"),
    (GoalType.API_ATTACK, ScenarioType.AUTH_BYPASS, "api-attack"),
    (GoalType.AGENTIC_TRUST_ABUSE, ScenarioType.CONFUSED_DEPUTY, "agentic-trust-abuse"),
    (GoalType.RECON_INFERENCE, ScenarioType.REFUSAL_ORACLE, "recon-inference"),
]


def _make_scenario(goal_type: GoalType, scenario_type: ScenarioType) -> AttackScenario:
    return AttackScenario(
        scenario_id="test-scenario",
        goal_type=goal_type,
        scenario_type=scenario_type,
        title="Unrelated Test Scenario Title",
        description="test",
    )


@pytest.mark.parametrize("goal_type,scenario_type,token", _GOAL_TYPE_CASES)
def test_canonical_token_matches_its_goal_type(goal_type, scenario_type, token):
    scenario = _make_scenario(goal_type, scenario_type)
    assert _scenario_matches_filter(scenario, {_normalize_scenario_token(token)}) is True


@pytest.mark.parametrize("goal_type,scenario_type,token", _GOAL_TYPE_CASES)
def test_canonical_token_does_not_match_other_goal_types(goal_type, scenario_type, token):
    """A token for one GoalType must not accidentally match a different GoalType."""
    for other_goal, other_scenario, _other_token in _GOAL_TYPE_CASES:
        if other_goal == goal_type:
            continue
        scenario = _make_scenario(other_goal, other_scenario)
        assert _scenario_matches_filter(scenario, {_normalize_scenario_token(token)}) is False, (
            f"Token {token!r} for {goal_type} unexpectedly matched {other_goal}"
        )


def test_prompt_injection_legacy_alias_matches_prompt_driven_threat():
    """The old (broken) 'prompt-injection' token must be aliased, not silently dropped."""
    scenario = _make_scenario(GoalType.PROMPT_DRIVEN_THREAT, ScenarioType.SKELETON_KEY)
    assert _scenario_matches_filter(scenario, {_normalize_scenario_token("prompt-injection")}) is True


def test_no_filter_matches_everything():
    scenario = _make_scenario(GoalType.DATA_EXFILTRATION, ScenarioType.DIRECT_PII_EXTRACTION)
    assert _scenario_matches_filter(scenario, set()) is True
