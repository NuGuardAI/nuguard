"""Unit tests for the redteam.scenarios destructive/non-destructive filter.

nuguard.redteam.executor.orchestrator._scenario_matches_filter /
finding_matches_scenario_filter accept only two normalized tokens —
'destructive' and 'non_destructive' — classifying via the same keyword
heuristic (_is_destructive_scenario / _is_destructive_finding) already used
to order destructive scenarios last in a run. Empty filters means both.
"""
from __future__ import annotations

from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.executor.orchestrator import (
    RedteamOrchestrator,
    _is_destructive_scenario,
    _normalize_scenario_token,
    _scenario_matches_filter,
    finding_matches_scenario_filter,
    validate_scenario_filter,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument


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


# ---------------------------------------------------------------------------
# Structural destructive classification (issue #561) — a credentialed
# direct-HTTP mutation step (target_path + write method + no strip_auth) is
# destructive regardless of what the scenario's title/description say. This
# is what makes mass-assignment/price-tampering scenarios (whose titles never
# contain a destructive keyword) correctly classified.
# ---------------------------------------------------------------------------

def _make_scenario_with_step(step: ExploitStep) -> AttackScenario:
    chain = ExploitChain(
        chain_id="c1",
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.MASS_ASSIGNMENT,
        steps=[step],
    )
    return AttackScenario(
        scenario_id="s1",
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.MASS_ASSIGNMENT,
        title="Mass Assignment — TestEndpoint",
        description="Send extra privilege fields",
        chain=chain,
    )


def test_credentialed_write_step_is_destructive_without_keyword_title() -> None:
    step = ExploitStep(
        step_id="c1_s1", step_type="INVOKE", description="",
        payload="", target_path="/api/users", http_method="POST",
    )
    assert _is_destructive_scenario(_make_scenario_with_step(step))


def test_strip_auth_write_step_is_not_destructive() -> None:
    """An unauthenticated write (e.g. build_auth_bypass) uses no real credentials."""
    step = ExploitStep(
        step_id="c1_s1", step_type="INVOKE", description="",
        payload="", target_path="/api/users", http_method="POST", strip_auth=True,
    )
    assert not _is_destructive_scenario(_make_scenario_with_step(step))


def test_credentialed_get_step_is_not_destructive() -> None:
    step = ExploitStep(
        step_id="c1_s1", step_type="INVOKE", description="",
        payload="", target_path="/api/users/1", http_method="GET",
    )
    assert not _is_destructive_scenario(_make_scenario_with_step(step))


# ---------------------------------------------------------------------------
# RedteamOrchestrator default scenario_filter (issue #561)
# ---------------------------------------------------------------------------

def test_orchestrator_defaults_to_non_destructive_when_unconfigured() -> None:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    orch = RedteamOrchestrator(sbom=sbom, target_url="http://target.test", concurrency=1)
    assert orch._scenario_filter == {"non_destructive"}
    assert any("non-destructive" in note for note in orch.config_notes)


def test_orchestrator_respects_explicit_scenario_filter() -> None:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    orch = RedteamOrchestrator(
        sbom=sbom, target_url="http://target.test", concurrency=1,
        scenario_filter=["destructive", "non-destructive"],
    )
    assert orch._scenario_filter == {"destructive", "non_destructive"}
    assert not any("non-destructive" in note for note in orch.config_notes)
