from __future__ import annotations

from nuguard.redteam.executor.turn_latency import TurnLatencyTracker


def test_no_observation_keeps_base_timeout() -> None:
    assert TurnLatencyTracker().timeout_for(180.0, 8) == 180.0


def test_slow_target_stretches_timeout() -> None:
    t = TurnLatencyTracker()
    t.observe(duration_s=86.0, turns_used=2)  # 43 s/turn
    assert t.timeout_for(180.0, 8) == 43.0 * 8 * 1.25  # 430 s


def test_timeout_is_capped() -> None:
    t = TurnLatencyTracker()
    t.observe(duration_s=600.0, turns_used=2)
    assert t.timeout_for(180.0, 12) == 180.0 * 4


def test_fast_target_never_shrinks_below_base() -> None:
    t = TurnLatencyTracker()
    t.observe(duration_s=4.0, turns_used=4)
    assert t.timeout_for(180.0, 8) == 180.0


def test_disabled_timeout_unchanged_and_bad_samples_ignored() -> None:
    t = TurnLatencyTracker()
    t.observe(10.0, 0)
    assert t.seconds_per_turn is None
    t.observe(50.0, 1)
    assert t.timeout_for(0.0, 8) == 0.0
