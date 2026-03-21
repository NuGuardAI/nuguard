"""Maps goal types and scenario types to compliance framework references."""
from __future__ import annotations

from nuguard.models.exploit_chain import GoalType, ScenarioType

_OWASP_LLM: dict[GoalType, str] = {
    GoalType.PROMPT_DRIVEN_THREAT: "LLM01 – Prompt Injection",
    GoalType.DATA_EXFILTRATION: "LLM06 – Sensitive Information Disclosure",
    GoalType.PRIVILEGE_ESCALATION: "LLM05 – Improper Output Handling",
    GoalType.TOOL_ABUSE: "LLM04 – Model Denial of Service",
    GoalType.POLICY_VIOLATION: "LLM01 – Prompt Injection",
    GoalType.MCP_TOXIC_FLOW: "LLM02 – Insecure Output Handling",
}

_OWASP_ASI: dict[GoalType, str] = {
    GoalType.PROMPT_DRIVEN_THREAT: "ASI01 – Agent Goal Hijack",
    GoalType.TOOL_ABUSE: "ASI02 – Tool Misuse and Exploitation",
    GoalType.PRIVILEGE_ESCALATION: "ASI03 – Identity and Privilege Abuse",
    GoalType.MCP_TOXIC_FLOW: "ASI04 – Agentic Supply Chain",
    GoalType.DATA_EXFILTRATION: "ASI10 – Rogue Agents",
    GoalType.POLICY_VIOLATION: "ASI09 – Human-Agent Trust Exploitation",
}


def owasp_llm_ref(goal_type: GoalType) -> str | None:
    """Return the OWASP LLM Top 10 reference for the given goal type."""
    return _OWASP_LLM.get(goal_type)


def owasp_asi_ref(goal_type: GoalType) -> str | None:
    """Return the OWASP ASI reference for the given goal type."""
    return _OWASP_ASI.get(goal_type)
