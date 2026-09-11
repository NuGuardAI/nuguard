"""Shared OWASP LLM Top 10 (2026) / OWASP Top 10 for Agentic Applications (2026) mappings.

Used by all three finding-producing packages (analysis, behavior, redteam) so every
Finding cites the same short-code format regardless of which package produced it.

Citations use the source documents' own short codes (e.g. "LLM01:2026", "ASI04") so they
stay stable across NuGuard releases even as the underlying OWASP entry descriptions evolve.
Mirrors the ``NGA_TO_ATLAS`` dict in ``atlas.py`` — same rule_id keys, same
"map every rule, no silent gaps" convention.

Three lookup tables, one per producer's natural key:
  - ``NGA_TO_OWASP``      — analysis/ static rules, keyed by NGA rule_id
  - ``BA_RULE_TO_OWASP``  — behavior/ static alignment rules, keyed by BA rule_id
  - ``GOAL_TYPE_TO_OWASP``— redteam/ scenarios, keyed by GoalType (coarse fallback;
    individual scenarios may set a more specific ref that takes precedence)

Sources:
  OWASP Top 10 for LLM Applications 2026 (genai.owasp.org)
  OWASP Top 10 for Agentic Applications 2026 (genai.owasp.org)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.models.exploit_chain import GoalType


@dataclass(frozen=True)
class RuleOwaspRefs:
    owasp_llm: tuple[str, ...] = field(default_factory=tuple)
    owasp_agentic: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# NGA-xxx (structural rules)
# ---------------------------------------------------------------------------

_NGA_STRUCTURAL: dict[str, RuleOwaspRefs] = {
    "NGA-001": RuleOwaspRefs(owasp_llm=("LLM02:2026",)),  # Sensitive Information Disclosure
    "NGA-002": RuleOwaspRefs(owasp_llm=("LLM01:2026",), owasp_agentic=("ASI01",)),  # Prompt Injection / Agent Goal Hijack
    "NGA-003": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI03",)),  # Sensitive Info Disclosure / Identity & Privilege Abuse
    "NGA-004": RuleOwaspRefs(owasp_llm=("LLM05:2026",)),  # Data and Model Poisoning (host compromise -> tampering)
    "NGA-005": RuleOwaspRefs(owasp_llm=("LLM02:2026",)),  # Sensitive Information Disclosure
    "NGA-006": RuleOwaspRefs(owasp_llm=("LLM06:2026",), owasp_agentic=("ASI02",)),  # Unbounded Consumption / Tool Misuse & Exploitation
    "NGA-007": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI03",)),  # Excessive Agency / Identity & Privilege Abuse
    "NGA-008": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # Supply Chain / Agentic Supply Chain Vulnerabilities
    "NGA-009": RuleOwaspRefs(owasp_agentic=("ASI08",)),  # Cascading Failures (no traceability -> undetected propagation)
    "NGA-010": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # CI/CD supply-chain compromise
    "NGA-011": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # CI/CD supply-chain compromise
    "NGA-012": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI09",)),  # Excessive Agency / Human-Agent Trust Exploitation
    "NGA-013": RuleOwaspRefs(owasp_agentic=("ASI08",)),  # Cascading Failures (no network segmentation -> blast radius)
    "NGA-014": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI04",)),  # Sensitive Info Disclosure via CI logs
    "NGA-015": RuleOwaspRefs(owasp_llm=("LLM06:2026",)),  # Unbounded Consumption
    "NGA-016": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # Supply Chain (mutable image tag)
    "NGA-017": RuleOwaspRefs(owasp_agentic=("ASI08",)),  # Cascading Failures (undetected degraded service)
    "NGA-018": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI06",)),  # Sensitive Info Disclosure / Memory & Context Poisoning
    "NGA-019": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI02", "ASI06")),  # Sensitive Info Disclosure / Tool Misuse / Memory Poisoning
    "NGA-020": RuleOwaspRefs(owasp_llm=("LLM01:2026",), owasp_agentic=("ASI01", "ASI07")),  # Prompt Injection / Agent Goal Hijack / Insecure Inter-Agent Communication
    "NGA-021": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI03",)),  # Sensitive Info Disclosure (IDOR) / Identity & Privilege Abuse
    "NGA-022": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI02", "ASI04")),  # Supply Chain / Tool Misuse / Agentic Supply Chain Vulnerabilities
    "NGA-023": RuleOwaspRefs(owasp_llm=("LLM09:2026",), owasp_agentic=("ASI06",)),  # Vector and Embedding Weaknesses / Memory & Context Poisoning
    "NGA-024": RuleOwaspRefs(owasp_agentic=("ASI07",)),  # Insecure Inter-Agent Communication
    "NGA-025": RuleOwaspRefs(owasp_llm=("LLM08:2026",)),  # Hidden Context Exposure
    "NGA-026": RuleOwaspRefs(owasp_llm=("LLM06:2026",), owasp_agentic=("ASI02",)),  # Unbounded Consumption / Tool Misuse & Exploitation
    "NGA-027": RuleOwaspRefs(owasp_llm=("LLM02:2026",)),  # Sensitive Information Disclosure (missing security headers)
    "NGA-028": RuleOwaspRefs(owasp_llm=("LLM02:2026",)),  # Sensitive Information Disclosure (permissive CORS)
    "NGA-029": RuleOwaspRefs(owasp_llm=("LLM02:2026",)),  # Sensitive Information Disclosure (verbose error leak)
    "NGA-030": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI03",)),  # Excessive Agency / Identity & Privilege Abuse (JWT alg confusion)
}

# ---------------------------------------------------------------------------
# NGA-SC-xxx (supply-chain rules)
# ---------------------------------------------------------------------------

_NGA_SUPPLY_CHAIN: dict[str, RuleOwaspRefs] = {
    "NGA-SC-001": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # OIDC + unpinned action
    "NGA-SC-002": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # dispatch/PR-target reaches publish
    "NGA-SC-003": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # OIDC + mutable checkout ref
    "NGA-SC-004": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # unpinned install / piped download
    "NGA-SC-005": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI04",)),  # CI reads creds/env/memory
    "NGA-SC-006": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # no provenance attestation
    "NGA-SC-007": RuleOwaspRefs(owasp_agentic=("ASI02", "ASI05")),  # unrestricted shell permission / RCE
    "NGA-SC-008": RuleOwaspRefs(owasp_agentic=("ASI04",)),  # repo-controlled agent config
    "NGA-SC-009": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # untrusted MCP server reference
    "NGA-SC-010": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI09",)),  # auto-run/auto-approve
    "NGA-SC-011": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # postinstall network download
    "NGA-SC-012": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04", "ASI05")),  # curl | bash lifecycle hook
    "NGA-SC-013": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04", "ASI05")),  # download+run Bun
    "NGA-SC-014": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI04",)),  # lifecycle script reads creds
    "NGA-SC-015": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # build hook network call
    "NGA-SC-016": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # obfuscated lifecycle script
    "NGA-SC-017": RuleOwaspRefs(owasp_agentic=("ASI04",)),  # oversized file in hidden AI-tool dir
    "NGA-SC-018": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI04",)),  # high-entropy blob (secret/payload)
    "NGA-SC-019": RuleOwaspRefs(owasp_agentic=("ASI04",)),  # minified obfuscated JS
    "NGA-SC-020": RuleOwaspRefs(owasp_agentic=("ASI04", "ASI10")),  # misleading commit message
    "NGA-SC-021": RuleOwaspRefs(owasp_agentic=("ASI04", "ASI10")),  # skip-ci on sensitive change
    "NGA-SC-022": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # workflow change w/o manifest change
    "NGA-SC-023": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # mutable dependency ref
    "NGA-SC-024": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # declared dep missing from lockfile
    "NGA-SC-025": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # known-malicious IOC match
}

NGA_TO_OWASP: dict[str, RuleOwaspRefs] = {**_NGA_STRUCTURAL, **_NGA_SUPPLY_CHAIN}


def owasp_refs_for_rule(rule_id: str) -> RuleOwaspRefs:
    """Return the OWASP citations for an analysis NGA *rule_id*, or empty if unmapped."""
    return NGA_TO_OWASP.get(rule_id, RuleOwaspRefs())


# ---------------------------------------------------------------------------
# BA-xxx (behavior static alignment rules — nuguard/behavior/alignment.py)
# ---------------------------------------------------------------------------

BA_RULE_TO_OWASP: dict[str, RuleOwaspRefs] = {
    "BA-001": RuleOwaspRefs(owasp_llm=("LLM08:2026",)),  # restricted topic in system prompt -> Hidden Context Exposure
    "BA-002": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI02",)),  # risky tool, no guardrail
    "BA-003": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI02",)),  # restricted-action tool reachable
    "BA-004": RuleOwaspRefs(owasp_llm=("LLM02:2026",)),  # PII datastore without guardrail
    "BA-005": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI03",)),  # no-auth agent, high-priv tool
    "BA-006": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # untrusted MCP server, write tool
    "BA-007": RuleOwaspRefs(owasp_llm=("LLM01:2026",), owasp_agentic=("ASI01",)),  # blocked_topics doesn't cover restricted_topics
    "BA-008": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI09",)),  # no HITL gate for hitl_triggers
    "BA-009": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI03",)),  # AUTH doesn't protect sensitive endpoints/agents
    "BA-010": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI03",)),  # high-priv component reachable w/o AUTH/GUARDRAIL
    "BA-011": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI06",)),  # DATASTORE write access lacks HITL/auth/guardrail
    "BA-012": RuleOwaspRefs(owasp_llm=("LLM02:2026",)),  # sensitive data reaches external MODEL
    "BA-013": RuleOwaspRefs(owasp_llm=("LLM08:2026",)),  # restricted topic in AGENT's PROMPT
    "BA-014": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI07",)),  # handoff to higher-priv agent w/o boundary
    "BA-015": RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),  # DEPLOYS path security posture issues
    "BA-016": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI03",)),  # API_ENDPOINT returns sensitive data w/o auth
}


def owasp_refs_for_ba_rule(rule_id: str) -> RuleOwaspRefs:
    """Return the OWASP citations for a behavior BA-*** *rule_id*, or empty if unmapped."""
    return BA_RULE_TO_OWASP.get(rule_id, RuleOwaspRefs())


# ---------------------------------------------------------------------------
# GoalType (redteam scenarios — coarse fallback keyed on the 9-value GoalType
# enum). Individual scenario builders may set a more specific owasp_llm_ref /
# owasp_asi_ref on the ExploitChain, which takes precedence over this table.
# ---------------------------------------------------------------------------

GOAL_TYPE_TO_OWASP: dict[GoalType, RuleOwaspRefs] = {
    GoalType.PROMPT_DRIVEN_THREAT: RuleOwaspRefs(owasp_llm=("LLM01:2026",), owasp_agentic=("ASI01",)),
    GoalType.DATA_EXFILTRATION: RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI10",)),
    GoalType.PRIVILEGE_ESCALATION: RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI03",)),
    GoalType.TOOL_ABUSE: RuleOwaspRefs(owasp_llm=("LLM06:2026",), owasp_agentic=("ASI02",)),
    GoalType.POLICY_VIOLATION: RuleOwaspRefs(owasp_llm=("LLM01:2026",), owasp_agentic=("ASI09",)),
    GoalType.MCP_TOXIC_FLOW: RuleOwaspRefs(owasp_llm=("LLM04:2026",), owasp_agentic=("ASI04",)),
    GoalType.API_ATTACK: RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI03",)),
    GoalType.AGENTIC_TRUST_ABUSE: RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI03",)),
    GoalType.RECON_INFERENCE: RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI02",)),
}


def owasp_refs_for_goal(goal_type: GoalType) -> RuleOwaspRefs:
    """Return the OWASP citations for a redteam *goal_type*, or empty if unmapped."""
    return GOAL_TYPE_TO_OWASP.get(goal_type, RuleOwaspRefs())


# ---------------------------------------------------------------------------
# BehaviorFindingType (behavior dynamic findings — nuguard/behavior/runner.py).
# Keyed by the plain finding_type string (nuguard.behavior.models.BehaviorFindingType
# values, plus "DATA_LEAK" for the canary-hit deviation_type). Coarse fallback, same
# role as GOAL_TYPE_TO_OWASP for redteam.
# ---------------------------------------------------------------------------

BEHAVIOR_FINDING_TYPE_TO_OWASP: dict[str, RuleOwaspRefs] = {
    "CAPABILITY_GAP": RuleOwaspRefs(owasp_agentic=("ASI08",)),
    "TOOL_CHAIN_BROKEN": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI08",)),
    "INTENT_MISALIGNMENT": RuleOwaspRefs(owasp_llm=("LLM01:2026",), owasp_agentic=("ASI09",)),
    "POLICY_VIOLATION": RuleOwaspRefs(owasp_llm=("LLM01:2026",), owasp_agentic=("ASI09",)),
    "SECRET_DISCLOSURE": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI10",)),
    "DATA_LEAK": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI10",)),
    "DATA_HANDLING_VIOLATION": RuleOwaspRefs(owasp_llm=("LLM02:2026",), owasp_agentic=("ASI06",)),
    "ESCALATION_BYPASS": RuleOwaspRefs(owasp_llm=("LLM03:2026",), owasp_agentic=("ASI03",)),
}


def owasp_refs_for_finding_type(finding_type: str) -> RuleOwaspRefs:
    """Return the OWASP citations for a behavior dynamic-finding *finding_type*."""
    return BEHAVIOR_FINDING_TYPE_TO_OWASP.get(str(finding_type).upper(), RuleOwaspRefs())
