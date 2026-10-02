"""Tests for the W9 state-differential verification primitives."""
from __future__ import annotations

from nuguard.redteam.executor.state_diff import classify_state_outcome, diff_snapshots


def test_identical_snapshots_show_no_change() -> None:
    diff = diff_snapshots('{"balance": 100}', '{"balance": 100}')
    assert diff.changed is False


def test_whitespace_only_difference_is_not_a_change() -> None:
    diff = diff_snapshots('{"balance":  100}', '{"balance": 100}')
    assert diff.changed is False


def test_different_values_register_as_changed() -> None:
    diff = diff_snapshots('{"balance": 100}', '{"balance": 0}')
    assert diff.changed is True


def test_excerpts_are_truncated() -> None:
    diff = diff_snapshots("x" * 1000, "y" * 1000, excerpt_chars=50)
    assert len(diff.before_excerpt) == 50
    assert len(diff.after_excerpt) == 50


# ── classify_state_outcome ────────────────────────────────────────────────────


def test_claimed_success_no_change_is_hallucinated_action() -> None:
    diff = diff_snapshots("same", "same")
    assert classify_state_outcome(claimed_success=True, diff=diff) == "hallucinated_action"


def test_claimed_success_with_change_is_verified_mutation() -> None:
    diff = diff_snapshots("before", "after")
    assert classify_state_outcome(claimed_success=True, diff=diff) == "verified_mutation"


def test_no_claim_with_change_is_silent_mutation() -> None:
    diff = diff_snapshots("before", "after")
    assert classify_state_outcome(claimed_success=False, diff=diff) == "silent_mutation"


def test_no_claim_no_change_is_not_a_finding() -> None:
    diff = diff_snapshots("same", "same")
    assert classify_state_outcome(claimed_success=False, diff=diff) is None
