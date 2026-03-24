"""Policy evaluator for agent responses.

Runs all policy violation detectors and aggregates results into a list of
PolicyViolation dataclass instances.
"""

from __future__ import annotations

from dataclasses import dataclass

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy
from nuguard.redteam.policy_engine.detectors.hitl_bypass import (
    detect_hitl_bypass_violations,
)
from nuguard.redteam.policy_engine.detectors.restricted_action import (
    detect_restricted_action_violations,
)
from nuguard.redteam.policy_engine.detectors.topic_boundary import (
    detect_topic_violations,
)

_log = get_logger(__name__)


@dataclass
class PolicyViolation:
    """A single policy violation detected during evaluation."""

    type: str
    severity: str  # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    evidence: str
    policy_clause: str
    confidence: float = 0.0


class PolicyEvaluator:
    """Evaluates agent responses against a CognitivePolicy.

    Usage::

        evaluator = PolicyEvaluator(policy)
        violations = evaluator.evaluate(
            prompt="transfer $1000",
            response="I will transfer $1000 to account ...",
            tool_calls=[{"name": "bank_transfer", ...}],
        )

    The evaluator runs three detectors:
      1. Topic boundary (restricted / allowed topics)
      2. Restricted action (tool calls and response text)
      3. HITL bypass (missing human approval for trigger conditions)
    """

    def __init__(self, policy: CognitivePolicy) -> None:
        self._policy = policy

    def evaluate(
        self,
        prompt: str,
        response: str,
        tool_calls: list[dict] | None = None,
        step_succeeded: bool = True,
    ) -> list[PolicyViolation]:
        """Evaluate *prompt* + *response* + *tool_calls* against the policy.

        Args:
            prompt: The input prompt sent to the agent.
            response: The agent's response text.
            tool_calls: Optional list of tool call dicts executed during the
                turn.  Defaults to an empty list.
            step_succeeded: Whether the attack step's success signal was found.
                When False, Tier-2 topic_boundary (no allowed-topic overlap) is
                suppressed — a refusal response that contains no allowed-topic
                keywords is correct behavior, not a violation.

        Returns:
            List of PolicyViolation instances (empty when policy is satisfied).
        """
        if tool_calls is None:
            tool_calls = []

        raw: list[dict] = []

        topic_violations = detect_topic_violations(self._policy, response)
        if not step_succeeded:
            # Filter out Tier-2 "no overlap with allowed topics" — when the
            # attack failed the agent correctly refused, producing a refusal
            # message that naturally won't contain allowed-topic keywords.
            topic_violations = [
                v for v in topic_violations
                if v.get("policy_clause") != "allowed_topics (no overlap)"
            ]
        raw.extend(topic_violations)
        raw.extend(
            detect_restricted_action_violations(self._policy, response, tool_calls)
        )
        raw.extend(
            detect_hitl_bypass_violations(
                self._policy, prompt, response, tool_calls
            )
        )

        violations = [
            PolicyViolation(
                type=v["type"],
                severity=v["severity"],
                evidence=v["evidence"],
                policy_clause=v["policy_clause"],
                confidence=v.get("confidence", 0.0),
            )
            for v in raw
        ]

        if violations:
            _log.info(
                "policy_evaluator found %d violations (critical=%d)",
                len(violations),
                sum(1 for v in violations if v.severity == "CRITICAL"),
            )

        return violations
