"""Unit tests for the redteam.scenarios destructive/non-destructive filter.

nuguard.redteam.executor.orchestrator._scenario_matches_filter /
finding_matches_scenario_filter accept only two normalized tokens —
'destructive' and 'non_destructive' — classifying via the same keyword
heuristic (_is_destructive_scenario / _is_destructive_finding) already used
to order destructive scenarios last in a run. Empty filters means both.
"""
from __future__ import annotations

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.executor.orchestrator import (
    _normalize_scenario_token,
    _scenario_matches_filter,
    finding_matches_scenario_filter,
    validate_scenario_filter,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario


def _make_scenario(title: str, description: str = "test") -> AttackScenario:
    return AttackScenario(
        scenario_id="s1",
        goal_type=GoalType.TOOL_ABUSE,
        scenario_type=ScenarioType.DESTRUCTIVE_RECORD_MUTATION,
        title=title,
        description=description,
    )


_DESTRUCTIVE_SCENARIO = _make_scenario("Delete User Record — TestAgent")
_NON_DESTRUCTIVE_SCENARIO = _make_scenario("Extract System Prompt — TestAgent")


def test_empty_filter_matches_both() -> None:
    assert _scenario_matches_filter(_DESTRUCTIVE_SCENARIO, set())
    assert _scenario_matches_filter(_NON_DESTRUCTIVE_SCENARIO, set())


def test_destructive_filter_matches_only_destructive() -> None:
    filt = {_normalize_scenario_token("destructive")}
    assert _scenario_matches_filter(_DESTRUCTIVE_SCENARIO, filt)
    assert not _scenario_matches_filter(_NON_DESTRUCTIVE_SCENARIO, filt)


def test_non_destructive_filter_matches_only_non_destructive() -> None:
    filt = {_normalize_scenario_token("non-destructive")}
    assert not _scenario_matches_filter(_DESTRUCTIVE_SCENARIO, filt)
    assert _scenario_matches_filter(_NON_DESTRUCTIVE_SCENARIO, filt)


def test_both_tokens_together_matches_everything() -> None:
    filt = {_normalize_scenario_token("destructive"), _normalize_scenario_token("non-destructive")}
    assert _scenario_matches_filter(_DESTRUCTIVE_SCENARIO, filt)
    assert _scenario_matches_filter(_NON_DESTRUCTIVE_SCENARIO, filt)


def test_agent_name_with_destructive_word_is_not_a_false_positive() -> None:
    """Only the attack-action portion of the title (before ' — ') is checked."""
    scenario = _make_scenario("Extract System Prompt — Cancellation Agent")
    filt = {_normalize_scenario_token("non-destructive")}
    assert _scenario_matches_filter(scenario, filt)


def test_validate_scenario_filter_accepts_destructive_tokens() -> None:
    assert validate_scenario_filter(["destructive", "non-destructive", "non_destructive"]) == []


def test_validate_scenario_filter_flags_old_family_tokens() -> None:
    """Regression guard: the old 9-family vocabulary must not silently work again."""
    unrecognized = validate_scenario_filter(["api-attack", "prompt-driven-threat", "totally-bogus"])
    assert unrecognized == ["api-attack", "prompt-driven-threat", "totally-bogus"]


def test_finding_matches_scenario_filter_empty_matches_all() -> None:
    finding = Finding(
        finding_id="F1",
        title="Delete User Record — TestAgent",
        description="test",
        severity=Severity.HIGH,
    )
    assert finding_matches_scenario_filter(finding, set())


def test_finding_matches_scenario_filter_destructive() -> None:
    destructive_finding = Finding(
        finding_id="F1",
        title="Delete User Record — TestAgent",
        description="test",
        severity=Severity.HIGH,
    )
    non_destructive_finding = Finding(
        finding_id="F2",
        title="Extract System Prompt — TestAgent",
        description="test",
        severity=Severity.HIGH,
    )
    filt = {_normalize_scenario_token("destructive")}
    assert finding_matches_scenario_filter(destructive_finding, filt)
    assert not finding_matches_scenario_filter(non_destructive_finding, filt)
