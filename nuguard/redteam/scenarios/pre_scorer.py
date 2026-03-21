"""Pre-scores exploit chains by estimated impact before execution."""
from __future__ import annotations

from nuguard.models.exploit_chain import ExploitChain, GoalType

# Base score by goal type (matches design doc §12)
_BASE_SCORES: dict[GoalType, float] = {
    GoalType.DATA_EXFILTRATION: 9.0,
    GoalType.PRIVILEGE_ESCALATION: 8.5,
    GoalType.MCP_TOXIC_FLOW: 8.0,
    GoalType.API_ATTACK: 8.0,
    GoalType.PROMPT_DRIVEN_THREAT: 7.5,
    GoalType.POLICY_VIOLATION: 7.0,
    GoalType.TOOL_ABUSE: 6.5,
}


def pre_score(
    chain: ExploitChain,
    pii_in_path: bool = False,
    phi_in_path: bool = False,
    has_unauth_entry: bool = False,
    has_no_auth_tool: bool = False,
    long_chain: bool = False,
) -> float:
    """Return an impact score in [0, 10] for the chain before execution."""
    base = _BASE_SCORES.get(chain.goal_type, 5.0)
    modifiers = 0.0
    if pii_in_path:
        modifiers += 1.0   # PII in scope (credit card, bank account, SSN, etc.)
    if phi_in_path:
        modifiers += 1.5   # PHI in scope (HIPAA-regulated; higher severity than generic PII)
    if has_unauth_entry:
        modifiers += 0.5   # Unauthenticated entry point
    if has_no_auth_tool:
        modifiers += 0.5   # Tool needs no auth
    if long_chain:
        modifiers -= 0.5   # Long chains are harder to execute, lower confidence
    return min(round(base + modifiers, 2), 10.0)
