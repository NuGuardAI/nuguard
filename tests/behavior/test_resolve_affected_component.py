"""Tests for _resolve_affected_component() — the shared finding attribution
fallback chain used across all four finding-construction sites in
nuguard/behavior/runner.py (policy-violation, canary, deviation, and
gap-bucketed findings).
"""
from __future__ import annotations

from nuguard.behavior.models import BehaviorScenario, BehaviorScenarioType
from nuguard.behavior.runner import _resolve_affected_component


def _make_scenario(**overrides) -> BehaviorScenario:
    defaults: dict = {
        "scenario_type": BehaviorScenarioType.INTENT_HAPPY_PATH,
        "name": "test_scenario",
    }
    defaults.update(overrides)
    return BehaviorScenario(**defaults)


def test_none_scenario_returns_unknown() -> None:
    assert _resolve_affected_component(None) == "unknown"


def test_nothing_set_returns_unknown() -> None:
    scenario = _make_scenario()
    assert _resolve_affected_component(scenario) == "unknown"


def test_target_component_takes_priority() -> None:
    scenario = _make_scenario(
        target_component="Seat Booking Agent",
        matched_topic="seat selection",
        primary_agent="Triage Agent",
        scoped_agents=["FAQ Agent"],
    )
    assert _resolve_affected_component(scenario) == "Seat Booking Agent"


def test_falls_back_to_matched_topic() -> None:
    scenario = _make_scenario(
        matched_topic="baggage claims",
        primary_agent="Triage Agent",
        scoped_agents=["FAQ Agent"],
    )
    assert _resolve_affected_component(scenario) == "baggage claims"


def test_falls_back_to_primary_agent() -> None:
    scenario = _make_scenario(
        primary_agent="Cancellation Agent",
        scoped_agents=["FAQ Agent"],
    )
    assert _resolve_affected_component(scenario) == "Cancellation Agent"


def test_falls_back_to_first_scoped_agent() -> None:
    scenario = _make_scenario(scoped_agents=["Flight Status Agent", "FAQ Agent"])
    assert _resolve_affected_component(scenario) == "Flight Status Agent"
