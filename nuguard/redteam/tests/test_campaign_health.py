"""Tests for HealthRegistry / CooldownQueue / RetryBudget (campaign increment 1)."""
from __future__ import annotations

from nuguard.common.transport import TransportOutcome as O
from nuguard.redteam.campaign.transport import (
    Action,
    CooldownQueue,
    HealthKey,
    HealthRegistry,
    RetryBudget,
)

CHAT = HealthKey("app", "/chat", "POST", "primary", "svc")
LOGIN = HealthKey("app", "/api/auth/login", "POST", "primary", "auth")


def test_dead_api_route_never_blocks_chat() -> None:
    h = HealthRegistry()
    for _ in range(5):
        h.record(LOGIN, O.HTTP_4XX, status=404)
    assert h.blocked_reason(LOGIN) == "route_dead_404"
    assert h.blocked_reason(CHAT) is None and not h.is_paused(CHAT, 0.0)
    assert not h.all_paused(0.0)  # no run-wide breaker exists


def test_intentional_unauthorized_404_is_evidence_not_health_failure() -> None:
    h = HealthRegistry()
    for _ in range(5):
        d = h.record(LOGIN, O.HTTP_4XX, status=404, unauthorized_probe=True)
        assert d.action == Action.CONTINUE
    assert h.blocked_reason(LOGIN) is None


def test_baseline_404_resolves_once_then_blocks() -> None:
    h = HealthRegistry()
    assert h.record(CHAT, O.HTTP_4XX, status=404, baseline=True).action == Action.RESOLVE_ROUTE
    assert h.record(CHAT, O.HTTP_4XX, status=404, baseline=True).action == Action.BLOCK_ROUTE
    assert h.blocked_reason(CHAT) is not None


def test_baseline_auth_handling() -> None:
    h = HealthRegistry()
    assert h.record(CHAT, O.HTTP_4XX, status=403, baseline=True).action == Action.CONTINUE
    assert h.record(CHAT, O.HTTP_4XX, status=401, baseline=True, can_refresh_auth=True).action == Action.REFRESH_AUTH
    assert h.record(CHAT, O.HTTP_4XX, status=401, baseline=True).action == Action.AUTH_BLOCKED
    assert h.blocked_reason(CHAT) == "auth_blocked:primary"
    # A denial on a non-baseline request is control evidence.
    assert h.record(LOGIN, O.HTTP_4XX, status=403).action == Action.CONTINUE


def test_429_honors_retry_after_and_cools_only_that_group() -> None:
    h = HealthRegistry()
    d = h.record(CHAT, O.RATE_LIMIT, retry_after=45.0, now=100.0)
    assert d.action == Action.RETRY_LATER and d.delay_seconds == 45.0
    assert h.is_paused(CHAT, 120.0) and not h.is_paused(CHAT, 146.0)
    assert not h.is_paused(LOGIN, 120.0)  # different dependency group


def test_cooldown_is_capped() -> None:
    h = HealthRegistry(max_cooldown_seconds=60.0)
    assert h.record(CHAT, O.RATE_LIMIT, retry_after=3600.0, now=0.0).delay_seconds == 60.0


def test_retry_budget_two_per_incident_per_120s() -> None:
    h = HealthRegistry(budget=RetryBudget(2, 120.0))
    assert h.record(CHAT, O.HTTP_GATEWAY_ERROR, now=0.0).action == Action.PAUSE_AND_PROBE
    assert h.record(CHAT, O.HTTP_GATEWAY_ERROR, now=10.0).action == Action.PAUSE_AND_PROBE
    assert h.record(CHAT, O.HTTP_GATEWAY_ERROR, now=20.0).action == Action.GIVE_UP
    assert h.record(CHAT, O.HTTP_GATEWAY_ERROR, now=200.0).action == Action.PAUSE_AND_PROBE  # window rolled


def test_ambiguous_write_failure_is_not_retried() -> None:
    h = HealthRegistry()
    d = h.record(CHAT, O.REQUEST_ERROR, write_capable=True, now=0.0)
    assert d.action == Action.EFFECT_UNKNOWN and not h.is_paused(CHAT, 0.0)


def test_one_recovery_probe_per_group_and_chat_baseline_flag() -> None:
    h = HealthRegistry()
    d = h.record(CHAT, O.HTTP_5XX, shares_service_with_chat=True, now=0.0)
    assert d.check_chat_baseline_first
    assert h.claim_probe(CHAT) and not h.claim_probe(CHAT)
    h.probe_result(CHAT, ok=True, now=5.0)
    assert not h.is_paused(CHAT, 5.0) and h.claim_probe(CHAT)


def test_failed_probe_keeps_group_paused_and_all_paused_reports() -> None:
    h = HealthRegistry(max_cooldown_seconds=60.0)
    h.record(CHAT, O.HTTP_5XX, now=0.0)
    h.claim_probe(CHAT)
    h.probe_result(CHAT, ok=False, now=1.0)
    assert h.is_paused(CHAT, 30.0) and h.all_paused(30.0)


def test_ok_clears_failure_state() -> None:
    h = HealthRegistry()
    h.record(LOGIN, O.HTTP_4XX, status=404)
    h.record(LOGIN, O.OK)
    h.record(LOGIN, O.HTTP_4XX, status=404)
    assert h.blocked_reason(LOGIN) is None  # consecutive count was reset


def test_cooldown_queue_orders_by_eligibility_without_sleeping() -> None:
    q: CooldownQueue[str] = CooldownQueue()
    q.push("b", 20.0)
    q.push("a", 10.0)
    q.push("c", 10.0)
    assert q.next_eligible_at() == 10.0 and len(q) == 3
    assert q.pop_ready(5.0) == []
    assert q.pop_ready(10.0) == ["a", "c"]  # stable for equal times
    assert q.pop_ready(25.0) == ["b"] and q.next_eligible_at() is None
