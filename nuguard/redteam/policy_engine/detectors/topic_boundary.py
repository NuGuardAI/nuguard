"""Topic boundary violation detector.

Performs two-tier keyword matching against policy topic lists:
  Tier 1 (HIGH)   — response contains a restricted_topic keyword.
  Tier 2 (MEDIUM) — allowed_topics defined AND response has no overlap with
                    any allowed_topic keyword.

No LLM is used.
"""

from __future__ import annotations

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy

_log = get_logger(__name__)


def detect_topic_violations(
    policy: CognitivePolicy,
    response: str,
) -> list[dict]:
    """Return a list of raw violation dicts for topic boundary checks.

    Each dict has keys: type, severity, evidence, policy_clause, confidence.

    Args:
        policy: Parsed CognitivePolicy.
        response: Agent response text to inspect.

    Returns:
        List of violation dicts (may be empty).
    """
    violations: list[dict] = []

    # Don't evaluate empty or very short responses — they provide no meaningful
    # signal and would cause Tier-2 to fire spuriously on failed/timed-out steps.
    if len(response.strip()) < 10:
        return violations

    response_lower = response.lower()

    # ---- Tier 1: restricted topics ----------------------------------------
    for topic in policy.restricted_topics:
        keyword = topic.strip().lower()
        if not keyword:
            continue
        if keyword in response_lower:
            _log.debug("topic_boundary Tier-1 hit: restricted topic %r", topic)
            violations.append(
                {
                    "type": "topic_boundary",
                    "severity": "HIGH",
                    "evidence": (
                        f"Response contains restricted topic keyword: {topic!r}"
                    ),
                    "policy_clause": f"restricted_topics: {topic!r}",
                    "confidence": 0.9,
                }
            )

    # ---- Tier 2: no overlap with allowed topics ---------------------------
    if policy.allowed_topics:
        matched_any_allowed = any(
            topic.strip().lower() in response_lower
            for topic in policy.allowed_topics
            if topic.strip()
        )
        if not matched_any_allowed:
            _log.debug(
                "topic_boundary Tier-2: response has no overlap with allowed topics"
            )
            violations.append(
                {
                    "type": "topic_boundary",
                    "severity": "MEDIUM",
                    "evidence": (
                        "Response has no overlap with any allowed_topics keyword. "
                        f"Allowed: {policy.allowed_topics!r}"
                    ),
                    "policy_clause": "allowed_topics (no overlap)",
                    "confidence": 0.7,
                }
            )

    return violations
