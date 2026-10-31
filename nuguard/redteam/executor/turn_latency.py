"""Adaptive per-scenario timeout derived from observed seconds-per-turn."""
from __future__ import annotations


class TurnLatencyTracker:
    """Tracks an exponential moving average of seconds spent per executed turn.

    Used to stretch ``scenario_timeout`` for multi-turn guided conversations
    when the target is slow, so a conversation that would fit
    ``turns x observed latency`` is not cancelled by a fixed budget.
    """

    def __init__(self, alpha: float = 0.3, headroom: float = 1.25, max_factor: float = 4.0) -> None:
        self._alpha = alpha
        self._headroom = headroom
        self._max_factor = max_factor
        self._ema: float | None = None

    @property
    def seconds_per_turn(self) -> float | None:
        """Current moving average, or ``None`` before any observation."""
        return self._ema

    def observe(self, duration_s: float, turns_used: int) -> None:
        """Record a completed scenario's wall-clock time and executed turns."""
        if turns_used <= 0 or duration_s <= 0:
            return
        sample = duration_s / turns_used
        self._ema = sample if self._ema is None else self._alpha * sample + (1 - self._alpha) * self._ema

    def timeout_for(self, base_timeout: float, max_turns: int) -> float:
        """Return the timeout for a scenario of *max_turns* turns.

        Never below *base_timeout*; never above ``base_timeout * max_factor``.
        A *base_timeout* of 0 (disabled) is returned unchanged.
        """
        if base_timeout <= 0 or self._ema is None or max_turns <= 0:
            return base_timeout
        wanted = self._ema * max_turns * self._headroom
        return min(max(base_timeout, wanted), base_timeout * self._max_factor)
