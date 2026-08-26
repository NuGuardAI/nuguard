"""Tests for nuguard/behavior/escalation.py — escalation ladder + circuit breaker."""
from __future__ import annotations

from nuguard.behavior.escalation import (
    STANDALONE_FAMILY,
    EscalationLadder,
    FamilyCircuitBreaker,
    tool_family,
)
from nuguard.behavior.refusal import RefusalReason


def test_tool_family_returns_standalone_for_none():
    assert tool_family(None) == STANDALONE_FAMILY
    assert tool_family("") == STANDALONE_FAMILY


def test_tool_family_returns_agent_name():
    assert tool_family("BillingAgent") == "BillingAgent"


class TestEscalationLadder:
    def test_attempt_2_is_explicit_phrasing(self):
        ladder = EscalationLadder()
        result = ladder.build(
            attempt=2,
            tool_name="CancelSubscription",
            tool_description="cancel a user's active subscription",
            refusal_reason=RefusalReason.OUT_OF_SCOPE_DEFLECTION,
            original_message="Can you cancel my subscription?",
        )
        assert result.strategy == "explicit_phrasing"
        assert "CancelSubscription" in result.message
        assert "cancel a user's active subscription" in result.message

    def test_attempt_3_missing_precondition_uses_setup_turn(self):
        ladder = EscalationLadder()
        result = ladder.build(
            attempt=3,
            tool_name="BookAppointment",
            tool_description="book a new appointment",
            refusal_reason=RefusalReason.MISSING_PRECONDITION,
            original_message="Can you book an appointment for me tomorrow?",
        )
        assert result.strategy == "precondition_setup"
        assert "book an appointment for me tomorrow" in result.message

    def test_attempt_3_non_precondition_reason_falls_back_to_explicit(self):
        ladder = EscalationLadder()
        result = ladder.build(
            attempt=3,
            tool_name="BookAppointment",
            tool_description="book a new appointment",
            refusal_reason=RefusalReason.OUT_OF_SCOPE_DEFLECTION,
            original_message="Can you book an appointment for me tomorrow?",
        )
        assert result.strategy == "explicit_phrasing"

    def test_build_falls_back_when_description_empty(self):
        ladder = EscalationLadder()
        result = ladder.build(
            attempt=2,
            tool_name="DoThing",
            tool_description="",
            refusal_reason=None,
            original_message="Can you do the thing?",
        )
        assert "perform its documented function" in result.message


class TestFamilyCircuitBreaker:
    def test_trips_after_threshold_consecutive_identical_reasons(self):
        breaker = FamilyCircuitBreaker(threshold=3)
        assert breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED) is False
        assert breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED) is False
        assert breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED) is True
        assert breaker.is_tripped("BillingAgent") is True

    def test_does_not_trip_on_mixed_reasons(self):
        breaker = FamilyCircuitBreaker(threshold=3)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        breaker.record("BillingAgent", RefusalReason.MISSING_PRECONDITION)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        assert breaker.is_tripped("BillingAgent") is False

    def test_success_resets_streak(self):
        breaker = FamilyCircuitBreaker(threshold=3)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        breaker.record("BillingAgent", None)  # genuine success resets
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        assert breaker.is_tripped("BillingAgent") is False

    def test_families_are_independent(self):
        breaker = FamilyCircuitBreaker(threshold=2)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        assert breaker.is_tripped("BillingAgent") is True
        assert breaker.is_tripped("SupportAgent") is False

    def test_once_tripped_stays_tripped_regardless_of_later_calls(self):
        breaker = FamilyCircuitBreaker(threshold=2)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        breaker.record("BillingAgent", RefusalReason.PERMISSION_DENIED)
        assert breaker.record("BillingAgent", None) is True
        assert breaker.is_tripped("BillingAgent") is True
