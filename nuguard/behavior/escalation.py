"""Escalation ladder and per-tool-family circuit breaker for refusal retries.

When a tool-coverage probe gets a canned refusal, a human QA tester doesn't
just give up — they try phrasing it more explicitly, or they realise the
agent is missing some context and supply it first. ``EscalationLadder``
automates that: given the refusal reason from
:mod:`nuguard.behavior.refusal`, it produces the next, more targeted attempt.

Modeled on :class:`nuguard.redteam.llm_engine.adaptive_mutation.AdaptiveMutationStrategy`
(failure classification -> targeted follow-up), but scoped to *coverage*
probes rather than adversarial payloads: the goal is to get a legitimate
tool invocation to fire, not to bypass a guardrail.

``FamilyCircuitBreaker`` mirrors the 3-consecutive-failures circuit breaker
in :mod:`nuguard.redteam.executor.orchestrator` (``_run_scenarios``), but
keyed per tool family (agent name, or the synthetic ``__standalone__`` group
for tools with no owning agent) and tripped by *identical refusal
classification* rather than transport failures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.behavior.refusal import RefusalReason
from nuguard.common.logging import get_logger

_log = get_logger(__name__)

# Synthetic family key for tools with no owning AGENT node (mirrors the
# standalone-group naming convention in nuguard.behavior.scenarios).
STANDALONE_FAMILY = "__standalone__"


def tool_family(primary_agent: str | None) -> str:
    """Return the circuit-breaker family key for a scenario/tool.

    Args:
        primary_agent: ``BehaviorScenario.primary_agent`` (or the tool's
            owning agent name), or ``None``/empty for standalone tools.
    """
    return primary_agent if primary_agent else STANDALONE_FAMILY


@dataclass
class EscalationAttempt:
    """A single rung of the escalation ladder for one coverage probe."""

    attempt: int
    message: str
    strategy: str
    """One of: natural_phrasing, explicit_phrasing, precondition_setup."""


class EscalationLadder:
    """Builds successively more explicit retry messages for a refused tool probe.

    Attempt 1 (natural phrasing) is the existing coverage-turn behaviour and
    is not produced by this class — callers only invoke
    :meth:`next_attempt` for attempts 2 and beyond, after a refusal has been
    observed.
    """

    def build(
        self,
        *,
        attempt: int,
        tool_name: str,
        tool_description: str,
        refusal_reason: RefusalReason | None,
        original_message: str,
    ) -> EscalationAttempt:
        """Return the escalated message for *attempt* (2 or 3).

        Args:
            attempt: The 1-indexed attempt number about to be sent (>= 2).
            tool_name: The SBOM tool's exact name.
            tool_description: The SBOM tool's description (used to spell out
                required parameters in the explicit-phrasing rung).
            refusal_reason: Why the *previous* attempt was refused.
            original_message: The natural-phrasing message from attempt 1,
                reused verbatim as the retry payload for the
                precondition-setup rung.
        """
        if attempt == 3 and refusal_reason == RefusalReason.MISSING_PRECONDITION:
            # Rung (c): the agent said it needs context it doesn't have.
            # Establish that context with a setup turn, then retry the
            # original phrasing verbatim on the turn after.
            setup = (
                f"Before we continue: please treat any account, ID, date, or "
                f"other reference details I've already given you in this "
                f"conversation as confirmed and on file. Now, {original_message.rstrip('.!? ')}"
            )
            return EscalationAttempt(attempt=attempt, message=setup, strategy="precondition_setup")

        # Rung (b) — and the fallback for rung (c) when the refusal wasn't a
        # missing-precondition case: explicit phrasing using the tool's exact
        # SBOM name/description, spelling out what it needs.
        desc = tool_description.strip() or "perform its documented function"
        return EscalationAttempt(
            attempt=attempt,
            message=(
                f"Please explicitly invoke the '{tool_name}' capability now — "
                f"it should {desc}. Use whatever details I've already provided "
                f"in this conversation and go ahead with it directly."
            ),
            strategy="explicit_phrasing",
        )


@dataclass
class FamilyCircuitBreaker:
    """Trips a tool family after N consecutive identical refusal classifications.

    Args:
        threshold: Number of consecutive identical-classification refusals
            (across escalation attempts, within a family) that trips the
            breaker for the rest of the run.
    """

    threshold: int = 3
    _last_reason: dict[str, RefusalReason] = field(default_factory=dict)
    _streak: dict[str, int] = field(default_factory=dict)
    _tripped: set[str] = field(default_factory=set)

    def record(self, family: str, reason: RefusalReason | None) -> bool:
        """Record one refusal outcome for *family*; return True if now tripped.

        A ``None`` reason (i.e. the attempt succeeded / was not a refusal)
        resets the streak for that family — one genuine success is enough to
        show the family is not systemically blocked.
        """
        if family in self._tripped:
            return True
        if reason is None:
            self._streak.pop(family, None)
            self._last_reason.pop(family, None)
            return False
        if self._last_reason.get(family) == reason:
            self._streak[family] = self._streak.get(family, 1) + 1
        else:
            self._last_reason[family] = reason
            self._streak[family] = 1
        if self._streak[family] >= self.threshold:
            self._tripped.add(family)
            _log.info(
                "FamilyCircuitBreaker: tripped for family=%s after %d consecutive %s refusals",
                family, self._streak[family], reason.value,
            )
            return True
        return False

    def is_tripped(self, family: str) -> bool:
        """Return True if *family* has already been circuit-broken."""
        return family in self._tripped
