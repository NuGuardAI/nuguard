"""Offline tests for the deterministic rate-limit evaluator (issue #766)."""
from __future__ import annotations

import pytest

from nuguard.redteam.executor.rate_limit_eval import (
    DEFAULT_PROBE_REQUESTS,
    SCANNER_MAX_PROBE_REQUESTS,
    probe_burst_count,
    RateLimitObservation,
    RateLimitPolicy,
    evaluate_rate_limit,
)

POLICY = RateLimitPolicy(max_requests=3, window_seconds=10.0)


def _obs(statuses, step=0.1, retries=None, timed=True, headers=None):
    retries = retries or {}
    headers = headers or {}
    return [
        RateLimitObservation(
            status_code=s,
            sent_at=i * step if timed else None,
            retries_429=retries.get(i, 0),
            headers=headers.get(i, {}),
        )
        for i, s in enumerate(statuses)
    ]


def test_unknown_policy_is_inconclusive_not_finding() -> None:
    ev = evaluate_rate_limit(_obs([200] * 5), None)
    assert ev.verdict == "inconclusive"
    assert ev.reason == "no_rate_limit_policy"
    assert ev.chain_status == "inconclusive:no_rate_limit_policy"


@pytest.mark.parametrize(
    "policy",
    [RateLimitPolicy(0, 10.0), RateLimitPolicy(3, 0.0), RateLimitPolicy(3, -1.0)],
)
def test_invalid_policy_is_inconclusive(policy) -> None:
    ev = evaluate_rate_limit(_obs([200] * 5), policy)
    assert (ev.verdict, ev.reason) == ("inconclusive", "invalid_policy")


def test_no_observations_is_inconclusive() -> None:
    ev = evaluate_rate_limit([], POLICY)
    assert (ev.verdict, ev.reason) == ("inconclusive", "no_observations")


def test_burst_within_policy_is_inconclusive() -> None:
    ev = evaluate_rate_limit(_obs([200, 200, 200]), POLICY)
    assert (ev.verdict, ev.reason) == ("inconclusive", "insufficient_observations")


def test_policy_exceeded_inside_window_without_429_is_finding() -> None:
    ev = evaluate_rate_limit(_obs([200] * 5), POLICY)
    assert (ev.verdict, ev.reason) == ("finding", "policy_exceeded_without_enforcement")
    assert ev.chain_status == "completed"


def test_burst_longer_than_window_is_inconclusive() -> None:
    ev = evaluate_rate_limit(_obs([200] * 5, step=20.0), POLICY)
    assert (ev.verdict, ev.reason) == ("inconclusive", "window_not_satisfied")


def test_missing_timestamps_are_inconclusive() -> None:
    ev = evaluate_rate_limit(_obs([200] * 5, timed=False), POLICY)
    assert (ev.verdict, ev.reason) == ("inconclusive", "no_timing")


def test_429_after_policy_limit_is_defended() -> None:
    ev = evaluate_rate_limit(_obs([200, 200, 200, 429, 429]), POLICY)
    assert (ev.verdict, ev.reason) == ("defended", "enforcement_observed")


def test_429_before_policy_limit_is_defended() -> None:
    ev = evaluate_rate_limit(_obs([200, 429, 429, 429, 429]), POLICY)
    assert ev.verdict == "defended"


def test_hidden_429_retry_counts_as_enforcement() -> None:
    # The client retried a 429 internally; the final response was a 200.
    ev = evaluate_rate_limit(_obs([200, 200, 200, 200, 200], retries={3: 1}), POLICY)
    assert (ev.verdict, ev.reason) == ("defended", "enforcement_observed")


def test_late_429_is_finding() -> None:
    ev = evaluate_rate_limit(_obs([200] * 5 + [429]), POLICY)
    assert (ev.verdict, ev.reason) == ("finding", "limit_not_enforced_at_policy")


def test_late_429_outside_window_is_inconclusive() -> None:
    ev = evaluate_rate_limit(_obs([200] * 5 + [429], step=20.0), POLICY)
    assert (ev.verdict, ev.reason) == ("inconclusive", "window_not_satisfied")


@pytest.mark.parametrize(
    ("statuses", "reason"),
    [
        ([200, 401, 401, 401, 401], "auth_rejected"),
        ([403] * 5, "auth_rejected"),
        ([404] * 5, "endpoint_not_found"),
        ([200, 200, 500, 200, 200], "server_error"),
        ([422] * 5, "unexpected_status"),
        ([0] * 5, "transport_error"),
        ([200, None, 200, 200, 200], "transport_error"),
    ],
)
def test_unusable_responses_are_inconclusive(statuses, reason) -> None:
    ev = evaluate_rate_limit(_obs(statuses), POLICY)
    assert (ev.verdict, ev.reason) == ("inconclusive", reason)
    assert ev.chain_status == f"inconclusive:{reason}"


def test_rate_limit_headers_are_recorded_in_evidence() -> None:
    ev = evaluate_rate_limit(
        _obs([200, 200, 200, 429], headers={3: {"retry-after": "30"}}), POLICY
    )
    assert ev.verdict == "defended"
    assert "retry-after" in ev.detail
    assert "@+0.30s" in ev.detail


def test_missing_headers_do_not_change_the_verdict() -> None:
    with_headers = evaluate_rate_limit(
        _obs([200] * 5, headers={0: {"x-ratelimit-limit": "100"}}), POLICY
    )
    without = evaluate_rate_limit(_obs([200] * 5), POLICY)
    assert with_headers.verdict == without.verdict == "finding"


def test_policy_at_scanner_cap_cannot_be_violated_by_the_burst() -> None:
    policy = RateLimitPolicy(max_requests=SCANNER_MAX_PROBE_REQUESTS, window_seconds=60.0)
    ev = evaluate_rate_limit(_obs([200] * SCANNER_MAX_PROBE_REQUESTS), policy)
    assert (ev.verdict, ev.reason) == ("inconclusive", "policy_exceeds_scanner_budget")


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(None, DEFAULT_PROBE_REQUESTS), (1, 2), (3, 4), (9, 10), (10, 10), (500, 10)],
)
def test_probe_burst_is_just_over_the_policy_and_capped_by_scanner_budget(limit, expected) -> None:
    assert probe_burst_count(limit) == expected
