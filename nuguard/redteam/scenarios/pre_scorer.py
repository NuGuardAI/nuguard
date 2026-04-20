"""Pre-scores exploit chains by estimated impact before execution."""
from __future__ import annotations

from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType

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

# Per-scenario-type overrides — applied before goal-type base when present.
# These encode attack-class-specific severity that cuts across goal types
# (e.g. SQL injection is always critical regardless of the parent goal).
_SCENARIO_SCORE_OVERRIDES: dict[str, float] = {
    ScenarioType.SQL_INJECTION.value: 8.5,
    ScenarioType.SSRF.value: 8.0,
    ScenarioType.RESTRICTED_ACTION.value: 7.5,
}


def pre_score(
    chain: ExploitChain,
    pii_in_path: bool = False,
    phi_in_path: bool = False,
    pfi_in_path: bool = False,
    has_unauth_entry: bool = False,
    has_no_auth_tool: bool = False,
    long_chain: bool = False,
) -> float:
    """Return an impact score in [0, 10] for the chain before execution."""
    base = _SCENARIO_SCORE_OVERRIDES.get(
        chain.scenario_type.value, _BASE_SCORES.get(chain.goal_type, 5.0)
    )
    modifiers = 0.0
    if pii_in_path:
        modifiers += 1.0   # PII in scope (names, emails, phone numbers, addresses)
    if phi_in_path:
        modifiers += 1.5   # PHI in scope (HIPAA-regulated; higher severity than generic PII)
    if pfi_in_path:
        modifiers += 1.2   # PFI in scope (PCI-DSS / GLBA: card numbers, bank accounts, SSN)
    if has_unauth_entry:
        modifiers += 0.5   # Unauthenticated entry point
    if has_no_auth_tool:
        modifiers += 0.5   # Tool needs no auth
    if long_chain:
        modifiers -= 0.5   # Long chains are harder to execute, lower confidence
    return min(round(base + modifiers, 2), 10.0)
