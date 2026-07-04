"""Data-classification (DLP) violation detector.

Checks whether response text or tool call arguments/results contain labels
that the Cognitive Policy marks as sensitive via ``data_classification``.

No LLM is used — detection is keyword-based (substring match) so it acts
as a cheap first-pass signal.  Callers may follow up with LLM evaluation
when the confidence threshold matters.
"""
from __future__ import annotations

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy

_log = get_logger(__name__)


def detect_data_classification_violations(
    policy: CognitivePolicy,
    prompt: str,
    response: str,
    tool_calls: list[dict],
) -> list[dict]:
    """Return violation dicts when classified data labels appear in response or tool output.

    Args:
        policy: Parsed CognitivePolicy.
        prompt: User/system prompt (checked for context but not counted as a violation source).
        response: Agent response text.
        tool_calls: List of tool call dicts executed by the agent.

    Returns:
        List of violation dicts (may be empty).
    """
    if not policy.data_classification:
        return []

    violations: list[dict] = []
    response_lower = response.lower()

    # Flatten tool call content for substring search
    tool_content_parts: list[str] = []
    for tc in tool_calls:
        # arguments / result may be nested dicts or strings
        for key in ("result", "output", "arguments", "content"):
            val = tc.get(key)
            if isinstance(val, str):
                tool_content_parts.append(val)
            elif isinstance(val, dict):
                tool_content_parts.append(str(val))
    tool_content = " ".join(tool_content_parts).lower()

    for label in policy.data_classification:
        label_lower = label.strip().lower()
        if not label_lower:
            continue

        hit_in_response = label_lower in response_lower
        hit_in_tools = label_lower in tool_content

        if not hit_in_response and not hit_in_tools:
            continue

        sources: list[str] = []
        if hit_in_response:
            sources.append("response")
        if hit_in_tools:
            sources.append("tool output")

        _log.debug(
            "data_classification HIGH: label %r found in %s",
            label,
            ", ".join(sources),
        )
        violations.append(
            {
                "type": "data_classification",
                "severity": "HIGH",
                "evidence": (
                    f"Data classification label {label!r} appeared in {', '.join(sources)}. "
                    "This may indicate leakage of sensitive or classified data."
                ),
                "policy_clause": f"data_classification: {label!r}",
                "confidence": 0.75,
            }
        )

    return violations
