"""Unit tests for scenario_filter matching in nuguard.redteam.executor.orchestrator.

Covers the ``prompt-injection``/``jailbreak`` alias fix: those config tokens
must match the real prompt-injection/jailbreak family (GoalType.PROMPT_DRIVEN_THREAT)
even though none of that family's ScenarioType values literally contain the
substring "prompt_injection" — only ScenarioType.REPO_PROMPT_INJECTION does.
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

# ScenarioType values that belong to the prompt-injection/jailbreak family —
# all carry GoalType.PROMPT_DRIVEN_THREAT but none contain "prompt_injection".
_PROMPT_INJECTION_FAMILY = [
    ScenarioType.CONTEXT_FLOODING,
    ScenarioType.STRUCTURAL_INJECTION,
    ScenarioType.INDIRECT_INJECTION,
    ScenarioType.MULTI_TURN_REDIRECTION,
    ScenarioType.SYSTEM_PROMPT_EXTRACTION,
    ScenarioType.GUARDRAIL_BYPASS,
    ScenarioType.MANY_SHOT_JAILBREAK,
    ScenarioType.CRESCENDO,
    ScenarioType.SKELETON_KEY,
    ScenarioType.PAYLOAD_SPLITTING,
    ScenarioType.FICTIONAL_FRAMING_BYPASS,
    ScenarioType.FALSE_POLICY_PREMISE,
]


def _make_scenario(scenario_type: ScenarioType, goal_type: GoalType = GoalType.PROMPT_DRIVEN_THREAT) -> AttackScenario:
    return AttackScenario(
        scenario_id="s1",
        goal_type=goal_type,
        scenario_type=scenario_type,
        title=f"{scenario_type.value.title()} — TestAgent",
        description="test",
    )


def test_prompt_injection_alias_matches_full_family() -> None:
    for st in _PROMPT_INJECTION_FAMILY:
        scenario = _make_scenario(st)
        assert _scenario_matches_filter(scenario, {_normalize_scenario_token("prompt-injection")}), (
            f"{st.value} should match the 'prompt-injection' filter alias"
        )


def test_jailbreak_alias_matches_full_family() -> None:
    for st in _PROMPT_INJECTION_FAMILY:
        scenario = _make_scenario(st)
        assert _scenario_matches_filter(scenario, {_normalize_scenario_token("jailbreak")})


def test_prompt_injection_alias_does_not_match_unrelated_goal() -> None:
    scenario = _make_scenario(ScenarioType.SQL_INJECTION, goal_type=GoalType.API_ATTACK)
    assert not _scenario_matches_filter(scenario, {_normalize_scenario_token("prompt-injection")})


def test_repo_prompt_injection_also_matches_prompt_injection_alias() -> None:
    # Coding-agent-specific type, not tagged PROMPT_DRIVEN_THREAT — included in
    # the alias set explicitly since its name is an obvious match.
    scenario = _make_scenario(ScenarioType.REPO_PROMPT_INJECTION, goal_type=GoalType.API_ATTACK)
    assert _scenario_matches_filter(scenario, {_normalize_scenario_token("prompt-injection")})


def test_existing_tokens_still_match_via_substring_fallback() -> None:
    """Regression guard: tokens without an alias entry keep working unchanged."""
    tool_abuse = _make_scenario(ScenarioType.CONFUSED_DEPUTY, goal_type=GoalType.TOOL_ABUSE)
    assert _scenario_matches_filter(tool_abuse, {_normalize_scenario_token("tool-abuse")})

    data_exfil = _make_scenario(ScenarioType.DIRECT_PII_EXTRACTION, goal_type=GoalType.DATA_EXFILTRATION)
    assert _scenario_matches_filter(data_exfil, {_normalize_scenario_token("data-exfiltration")})


def test_validate_scenario_filter_accepts_alias_tokens() -> None:
    assert validate_scenario_filter(["prompt-injection", "jailbreak"]) == []


def test_validate_scenario_filter_still_flags_unknown_tokens() -> None:
    unrecognized = validate_scenario_filter(["totally-bogus-token-xyz"])
    assert unrecognized == ["totally-bogus-token-xyz"]


def test_finding_matches_scenario_filter_uses_alias_too() -> None:
    finding = Finding(
        finding_id="F1",
        title="Skeleton Key — TestAgent",
        description="test",
        severity=Severity.HIGH,
        goal_type=GoalType.PROMPT_DRIVEN_THREAT.value,
        scenario_type=ScenarioType.SKELETON_KEY.value,
    )
    assert finding_matches_scenario_filter(finding, {_normalize_scenario_token("prompt-injection")})
