"""Coverage for the redteam.scenarios goal-type filter (nuguard.yaml `scenarios:`).

Asserts every canonical token for all 9 GoalTypes actually matches a scenario
of that type, and that tokens don't cross-match unrelated GoalTypes.
"""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.models.finding import Finding
from nuguard.redteam.executor.orchestrator import (
    _normalize_scenario_token,
    _scenario_matches_filter,
    finding_matches_scenario_filter,
    validate_scenario_filter,
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


def test_no_filter_matches_everything():
    scenario = _make_scenario(GoalType.DATA_EXFILTRATION, ScenarioType.DIRECT_PII_EXTRACTION)
    assert _scenario_matches_filter(scenario, set()) is True


class TestValidateScenarioFilter:
    @pytest.mark.parametrize("goal_type,scenario_type,token", _GOAL_TYPE_CASES)
    def test_canonical_tokens_are_all_recognized(self, goal_type, scenario_type, token):
        assert validate_scenario_filter([token]) == []

    def test_invalid_token_flagged(self):
        # Note: "prompt-injection" is deliberately NOT used here — it coincidentally
        # substring-matches ScenarioType.REPO_PROMPT_INJECTION, so it's "recognized"
        # by design even though it doesn't match the GoalType a user likely intended.
        assert validate_scenario_filter(["not-a-real-scenario-xyz"]) == ["not-a-real-scenario-xyz"]

    def test_mixed_valid_and_invalid(self):
        result = validate_scenario_filter(["policy-violation", "not-a-real-scenario-xyz"])
        assert result == ["not-a-real-scenario-xyz"]

    def test_empty_filter_returns_empty(self):
        assert validate_scenario_filter([]) == []

    def test_scenario_type_token_is_recognized(self):
        # ScenarioType values (not just GoalType) should also be accepted.
        assert validate_scenario_filter(["guardrail-bypass"]) == []


def _make_finding(
    *,
    goal_type: str | None,
    scenario_type: str | None = None,
    title: str = "Unrelated Test Finding Title",
) -> Finding:
    return Finding(
        finding_id="f1",
        title=title,
        severity="high",
        description="test",
        goal_type=goal_type,
        scenario_type=scenario_type,
    )


class TestFindingMatchesScenarioFilter:
    """finding_matches_scenario_filter() is run_redteam()'s post-run
    re-check against the findings the orchestrator actually returned. It
    must accept everything the orchestrator's own pre-run filter
    (_scenario_matches_filter) would have let through, or a scenario that
    was correctly selected to run ends up having its finding silently
    dropped from the final output — the exact bug this covers."""

    def test_regression_scenario_type_level_filter_no_longer_drops_finding(self) -> None:
        # The exact reported bug: goal_type is PROMPT_DRIVEN_THREAT (not a
        # substring match for the filter token below), but scenario_type is
        # the specific technique the user actually filtered for.
        finding = _make_finding(goal_type="PROMPT_DRIVEN_THREAT", scenario_type="APPROVAL_STATE_FORGERY")
        assert finding_matches_scenario_filter(finding, {"approval_state_forgery"}) is True

    def test_goal_type_level_filter_still_works(self) -> None:
        finding = _make_finding(goal_type="PROMPT_DRIVEN_THREAT", scenario_type="APPROVAL_STATE_FORGERY")
        assert finding_matches_scenario_filter(finding, {"prompt_driven_threat"}) is True

    def test_title_level_filter_still_works(self) -> None:
        # Title matching only normalizes hyphens/case, not spaces (mirroring
        # _scenario_matches_filter exactly) — so a matching token must use
        # spaces the same way the title does, not underscores.
        finding = _make_finding(
            goal_type="PROMPT_DRIVEN_THREAT",
            scenario_type="SYSTEM_PROMPT_EXTRACTION",
            title="Guided System Prompt Leak - Support Agent",
        )
        assert finding_matches_scenario_filter(finding, {"system prompt leak"}) is True

    def test_unrelated_scenario_type_filter_does_not_match(self) -> None:
        finding = _make_finding(goal_type="DATA_EXFILTRATION", scenario_type="DIRECT_PII_EXTRACTION")
        assert finding_matches_scenario_filter(finding, {"approval_state_forgery"}) is False

    def test_no_filter_matches_everything(self) -> None:
        finding = _make_finding(goal_type="PROMPT_DRIVEN_THREAT", scenario_type="APPROVAL_STATE_FORGERY")
        assert finding_matches_scenario_filter(finding, set()) is True

    def test_finding_without_goal_type_always_matches(self) -> None:
        # Preserves the pre-fix behaviour: a finding that never set goal_type
        # (e.g. from a non-redteam origin reusing this same filter) must not
        # be dropped just because it can't be matched against anything.
        finding = _make_finding(goal_type=None, scenario_type=None)
        assert finding_matches_scenario_filter(finding, {"approval_state_forgery"}) is True

    def test_finding_without_scenario_type_falls_back_to_goal_type_and_title(self) -> None:
        # Pre-scenario_type data (or behavior/analysis findings, which never
        # set it) must still work via goal_type/title alone, unchanged.
        finding = _make_finding(goal_type="DATA_EXFILTRATION", scenario_type=None)
        assert finding_matches_scenario_filter(finding, {"data_exfiltration"}) is True
        assert finding_matches_scenario_filter(finding, {"approval_state_forgery"}) is False
