"""Restricted action violation detector.

Checks tool calls and response text against policy.restricted_actions:
  - Tool name matches a restricted action → CRITICAL
  - Response text contains a phrase matching a restricted action → HIGH

No LLM is used.
"""

from __future__ import annotations

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy

_log = get_logger(__name__)


def _fuzzy_match(needle: str, haystack: str) -> bool:
    """Case-insensitive substring match in either direction."""
    n = needle.lower()
    h = haystack.lower()
    return n in h or h in n


def detect_restricted_action_violations(
    policy: CognitivePolicy,
    response: str,
    tool_calls: list[dict],
) -> list[dict]:
    """Return violation dicts for restricted action checks.

    Args:
        policy: Parsed CognitivePolicy.
        response: Agent response text.
        tool_calls: List of tool call dicts (each expected to have a ``name``
            or ``function.name`` key).

    Returns:
        List of violation dicts (may be empty).
    """
    violations: list[dict] = []
    response_lower = response.lower()

    def _tool_name(tc: dict) -> str:
        # Support both OpenAI-style {function: {name: ...}} and flat {name: ...}
        fn = tc.get("function") or {}
        return str(tc.get("name") or fn.get("name") or "").strip()

    for action in policy.restricted_actions:
        action_lower = action.strip().lower()
        if not action_lower:
            continue

        # ---- CRITICAL: tool call name matches --------------------------------
        for tc in tool_calls:
            tc_name = _tool_name(tc).lower()
            if tc_name and _fuzzy_match(action_lower, tc_name):
                _log.debug(
                    "restricted_action CRITICAL: tool %r matches action %r",
                    tc_name,
                    action,
                )
                violations.append(
                    {
                        "type": "restricted_action",
                        "severity": "CRITICAL",
                        "evidence": (
                            f"Tool call {_tool_name(tc)!r} matches restricted action {action!r}"
                        ),
                        "policy_clause": f"restricted_actions: {action!r}",
                        "confidence": 0.95,
                    }
                )

        # ---- HIGH: response text mentions the action -------------------------
        if action_lower in response_lower:
            _log.debug(
                "restricted_action HIGH: response contains action %r", action
            )
            violations.append(
                {
                    "type": "restricted_action",
                    "severity": "HIGH",
                    "evidence": (
                        f"Response text contains restricted action phrase: {action!r}"
                    ),
                    "policy_clause": f"restricted_actions: {action!r}",
                    "confidence": 0.8,
                }
            )

    return violations
