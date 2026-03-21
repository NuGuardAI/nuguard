"""Template-based remediation advice generator."""
from __future__ import annotations

from nuguard.models.exploit_chain import GoalType, ScenarioType

# Per-violation-type remediations used when a policy violation is detected
# against a scenario that has a different primary goal type (e.g. a TOPIC_BOUNDARY
# violation surfaced during an API_ATTACK scenario).
_VIOLATION_TYPE_TEMPLATES: dict[str, str] = {
    "topic_boundary": (
        "Add an output guardrail that restricts agent responses to the defined "
        "allowed_topics list. Responses that do not relate to permitted topics should "
        "be blocked or rewritten before being returned to the caller."
    ),
    "restricted_action": (
        "Enforce restricted actions at the guardrail layer — the system prompt alone "
        "is insufficient. Add an explicit check that intercepts and blocks any attempt "
        "to execute the restricted action before it reaches a tool or downstream service."
    ),
    "hitl_bypass": (
        "Add a mandatory human-in-the-loop enforcement node on every action path that "
        "matches a HITL trigger. Verbal or implicit approval must not substitute for an "
        "explicit out-of-band confirmation step."
    ),
}

_TEMPLATES: dict[GoalType, str] = {
    GoalType.PROMPT_DRIVEN_THREAT: (
        "Add an output guardrail that blocks responses containing system-prompt text. "
        "Validate that instructions from retrieved content cannot override system instructions."
    ),
    GoalType.DATA_EXFILTRATION: (
        "Add a GUARDRAIL node that filters PII/PHI field names from agent responses. "
        "Enforce field-level access controls in the datastore query layer."
    ),
    GoalType.PRIVILEGE_ESCALATION: (
        "Require authentication on all tools that write to datastores or execute code. "
        "Add a GUARDRAIL on the path from the entry-point tool to the high-privilege tool."
    ),
    GoalType.TOOL_ABUSE: (
        "Sanitise all string parameters passed to tools before using them in queries. "
        "Use parameterised queries to prevent SQL injection. "
        "Block private IP ranges in URL parameters to prevent SSRF."
    ),
    GoalType.POLICY_VIOLATION: (
        "Enforce restricted actions at the guardrail layer, not just in the system prompt. "
        "Add an explicit HITL enforcement node on all high-impact action paths."
    ),
    GoalType.MCP_TOXIC_FLOW: (
        "Sanitise all content read from untrusted MCP tools before passing to write-capable tools. "
        "Add an output guardrail that blocks instruction-like patterns in tool-sourced content."
    ),
    GoalType.API_ATTACK: (
        "Enforce authentication on every API endpoint — verify tokens server-side on each request. "
        "Apply object-level authorisation checks (IDOR): confirm the requesting principal owns the "
        "resource before returning it.  Use an allowlist of accepted fields to prevent mass "
        "assignment; never bind request body directly to privileged model fields."
    ),
}


def generate(
    goal_type: GoalType,
    affected_component: str = "",
    violation_type: str | None = None,
) -> str:
    """Return remediation guidance for the given goal type and affected component.

    When *violation_type* is provided (e.g. ``"topic_boundary"``), the
    violation-specific template takes precedence over the goal-type template so
    that policy-violation findings surfaced during non-policy-goal scenarios
    receive accurate remediation advice.
    """
    if violation_type:
        base = _VIOLATION_TYPE_TEMPLATES.get(violation_type) or _TEMPLATES.get(
            goal_type,
            "Review the affected component and add appropriate guardrails.",
        )
    else:
        base = _TEMPLATES.get(
            goal_type,
            "Review the affected component and add appropriate guardrails.",
        )
    if affected_component:
        return f"For {affected_component}: {base}"
    return base
