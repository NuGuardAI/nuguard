"""BehaviorEvidenceBundle — aggregates fast-path judge signals for one turn.

Mirrors the two-stage signal-collection-then-resolve architecture used by
:mod:`nuguard.redteam.llm_engine.evidence_bundle`, scoped to behavior's
PASS/PARTIAL/FAIL + 3-dimension rubric model rather than redteam's
succeeded/refusal_reason attack vocabulary — the two are structurally
similar but describe different things, so this is a fresh, smaller version
rather than a shared import.

Collecting signals into a bundle instead of resolving them with an ordered
``if/elif`` chain matters when two heuristics disagree (e.g. a response
contains both a refusal phrase and a happy-path completion phrase): an
ordered chain silently picks whichever pattern happens to be checked first,
while a bundle can recognise the conflict and defer to the LLM judge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Signal:
    """One piece of evidence contributed by a fast-path detector.

    Attributes:
        name: Short identifier for the detector that produced this signal
            (e.g. ``"clear_refusal"``, ``"happy_confirm"``).
        trust: ``"deterministic"`` for signals that are unambiguous by
            construction (e.g. an HTTP error status line); ``"heuristic"``
            for regex pattern matches that are usually but not always right.
        polarity: ``"pass"`` when the signal indicates a good outcome,
            ``"fail"`` when it indicates a bad one. Used only for conflict
            detection — the actual verdict string is ``verdict_str``.
        verdict_str: The ``TurnVerdict.verdict`` this signal resolves to
            when it wins (``"PASS"``, ``"PARTIAL"``, or ``"FAIL"``).
        reasoning: Short human-readable reason, stored on the resolved
            ``TurnVerdict.reasoning``.
        evidence: The matched text or a short description, used both in the
            resolved verdict's ``evidence`` field and in LLM prompt context.
        score_overrides: Optional per-dimension score overrides to apply on
            top of the flat PASS/FAIL defaults (mirrors the previous
            ``_fast_verdict`` behaviour of passing ``score_overrides`` to
            ``_make_fast_structural``).
    """

    name: str
    trust: Literal["deterministic", "heuristic"]
    polarity: Literal["pass", "fail"]
    verdict_str: Literal["PASS", "PARTIAL", "FAIL"]
    reasoning: str
    evidence: str
    score_overrides: dict[str, float] | None = None


@dataclass
class BehaviorEvidenceBundle:
    """All fast-path signals collected for one turn's verdict resolution."""

    signals: list[Signal] = field(default_factory=list)

    def add(self, found: list[Signal]) -> None:
        self.signals.extend(found)

    def _matching(self, *, trust: str, polarity: str) -> list[Signal]:
        return [s for s in self.signals if s.trust == trust and s.polarity == polarity]

    @property
    def deterministic(self) -> list[Signal]:
        return [s for s in self.signals if s.trust == "deterministic"]

    @property
    def heuristic_pass(self) -> list[Signal]:
        return self._matching(trust="heuristic", polarity="pass")

    @property
    def heuristic_fail(self) -> list[Signal]:
        return self._matching(trust="heuristic", polarity="fail")

    def has_conflict(self) -> bool:
        """True when heuristic evidence points both ways — needs the LLM judge."""
        return bool(self.heuristic_pass) and bool(self.heuristic_fail)

    def describe(self) -> str:
        """Render the bundle as a bullet list for LLM prompt context."""
        if not self.signals:
            return "(no heuristic signals detected)"
        lines = []
        for s in self.signals:
            arrow = "+" if s.polarity == "pass" else "-"
            lines.append(f"  [{arrow}] {s.name} ({s.trust}, strength=1.0): {s.evidence}")
        return "\n".join(lines)

    def resolve(self) -> tuple[Signal, Literal["high", "medium"]] | None:
        """Return ``(winning_signal, confidence)`` when resolvable, else None.

        Resolution policy, in order:
          1. Any deterministic-trust signal resolves outright, confidence="high".
          2. Conflicting heuristic signals (see :meth:`has_conflict`) cannot
             be resolved here — returns None so the caller falls through to
             the LLM judge.
          3. A single heuristic-fail signal with no pass counter-signal
             resolves fail, confidence="high".
          4. A single heuristic-pass signal with no fail counter-signal
             resolves pass, confidence="medium".
          5. No signals at all — returns None; nothing to resolve.
        """
        if self.deterministic:
            return self.deterministic[0], "high"
        if self.has_conflict():
            return None
        if self.heuristic_fail:
            return self.heuristic_fail[0], "high"
        if self.heuristic_pass:
            return self.heuristic_pass[0], "medium"
        return None
