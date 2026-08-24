"""Named, domain-adaptive engagement phases for ``redteam.mode: progressive``.

Maps ``docs/redteam-improve-2.md``'s 13-phase pentest-style methodology
(Phase 0 - Security Invariants ... Phase 12 - Recovery Testing) onto NuGuard's
existing ``ScenarioType`` taxonomy. This is deliberately kept separate from
``scenarios/generator.py``'s ``_ATTACK_PHASE``/``attack_phase_for`` (the
default ``concurrent`` mode's 1-9 phase table) so progressive mode is purely
additive — default behavior, tests, and the ``attack_phase`` field semantics
for existing users are unaffected. See docs/claude-redteam-3.md §4.
"""
from __future__ import annotations

from dataclasses import dataclass

from nuguard.models.exploit_chain import ScenarioType


@dataclass(frozen=True)
class Phase:
    """One named phase of a progressive engagement."""

    id: int
    name: str
    purpose: str
    scenario_types: tuple[ScenarioType, ...]
    # Which CognitivePolicy fields (or "owasp-genai-principle") feed the
    # security invariants this phase is meant to validate. See invariants.py.
    invariant_sources: tuple[str, ...] = ()


PROGRESSIVE_PHASES: tuple[Phase, ...] = (
    Phase(
        id=0,
        name="Security Invariants",
        purpose="Define pass/fail criteria before any adversarial testing begins.",
        scenario_types=(),
        invariant_sources=(
            "policy:restricted_topics",
            "policy:restricted_actions",
            "policy:hitl_tool_conditions",
            "owasp-genai-principle",
        ),
    ),
    Phase(
        id=1,
        name="Boundary Discovery",
        purpose="Map what the agent believes it can do (tools, systems, data) via low-risk probes.",
        scenario_types=(ScenarioType.REFUSAL_ORACLE, ScenarioType.BOUNDARY_SELF_PROBE),
    ),
    Phase(
        id=2,
        name="Off-Topic Resistance",
        purpose="Confirm the agent stays inside its declared policy scope.",
        scenario_types=(ScenarioType.OFF_TOPIC_PROBE,),
        invariant_sources=("policy:restricted_topics",),
    ),
    Phase(
        id=3,
        name="Identity Confusion",
        purpose="Attempt to blur authenticated identity via conversational claims alone.",
        scenario_types=(
            ScenarioType.SCRIPTED_IDENTITY_ESCALATION,
            ScenarioType.DELEGATED_IDENTITY_CONFUSION,
        ),
        invariant_sources=("owasp-genai-principle",),
    ),
    Phase(
        id=4,
        name="Role Confusion",
        purpose="Attempt authority escalation via conversational claims of elevated role.",
        scenario_types=(
            ScenarioType.SCRIPTED_ROLE_ESCALATION,
            ScenarioType.SKELETON_KEY,
            ScenarioType.GUARDRAIL_BYPASS,
        ),
        invariant_sources=("policy:restricted_actions", "owasp-genai-principle"),
    ),
    Phase(
        id=5,
        name="Instruction Hierarchy",
        purpose="Check whether higher-priority system/developer instructions override user requests.",
        scenario_types=(
            ScenarioType.STRUCTURAL_INJECTION,
            ScenarioType.POLICY_PATCHING,
            ScenarioType.PREMISE_INJECTION,
            ScenarioType.SYSTEM_PROMPT_EXTRACTION,
            ScenarioType.APPROVAL_STATE_FORGERY,
            ScenarioType.SESSION_METADATA_FORGERY,
        ),
        invariant_sources=("owasp-genai-principle",),
    ),
    Phase(
        id=6,
        name="Prompt Injection Resilience",
        purpose="Test resistance to malicious instructions embedded in retrieved content or uploaded documents.",
        scenario_types=(
            ScenarioType.INDIRECT_INJECTION,
            ScenarioType.RAG_POISONING,
            ScenarioType.RAG_DOCUMENT_POISONING,
            ScenarioType.RAG_ACL_BYPASS,
            ScenarioType.RAG_EMBEDDING_HIJACK,
            ScenarioType.RAG_CHUNK_SMUGGLING,
        ),
        invariant_sources=("policy:restricted_actions", "owasp-genai-principle"),
    ),
    Phase(
        id=7,
        name="Cross-Customer Isolation",
        purpose="Test strict tenant isolation — no cross-session/cross-customer leakage.",
        scenario_types=(
            ScenarioType.CROSS_TENANT_EXFILTRATION,
            ScenarioType.CROSS_SESSION_LEAK,
            ScenarioType.ACCOUNT_ID_PROBE,
        ),
        invariant_sources=("owasp-genai-principle",),
    ),
    Phase(
        id=8,
        name="Memory Poisoning",
        purpose="Attempt to replace or modify long-term memory / imported documents with malicious instructions.",
        scenario_types=(
            ScenarioType.DOCUMENT_MEMORY_POISONING,
            ScenarioType.MEMORY_POISONING,
            ScenarioType.FALSE_IDENTITY_MEMORY,
            ScenarioType.SUMMARY_POISONING,
            ScenarioType.MEMORY_AUTH_DRIFT,
            ScenarioType.PROFILE_FIELD_POISONING,
            ScenarioType.CROSS_SESSION_BACKDOOR,
            ScenarioType.CREDENTIAL_PERSISTENCE,
        ),
        invariant_sources=("policy:restricted_actions",),
    ),
    Phase(
        id=9,
        name="Tool Invocation Validation & HITL",
        purpose="Verify authorization, consent, and business-rule checks before high-impact tool execution.",
        scenario_types=(
            ScenarioType.HITL_BYPASS,
            ScenarioType.RESTRICTED_ACTION,
            ScenarioType.APPROVAL_SUMMARY_MISMATCH,
            ScenarioType.CONSENT_LAUNDERING,
            ScenarioType.PARTIAL_APPROVAL_OVERREACH,
            ScenarioType.UPSTREAM_APPROVAL_FABRICATION,
        ),
        invariant_sources=("policy:hitl_tool_conditions", "policy:restricted_actions"),
    ),
    Phase(
        id=10,
        name="Sensitive Data Protection",
        purpose="Verify consistent enforcement of authorization boundaries for data outside the user's scope.",
        scenario_types=(
            ScenarioType.DIRECT_PII_EXTRACTION,
            ScenarioType.AGGREGATED_PII,
            ScenarioType.PRIVATE_DOC_EXTRACTION,
            ScenarioType.SENSITIVE_HISTORY_DISCLOSURE,
            ScenarioType.COVERT_ENCODING,
            ScenarioType.BULK_DATA_EXPORT,
        ),
        invariant_sources=("policy:data_classification", "policy:restricted_topics"),
    ),
    Phase(
        id=11,
        name="Multi-Agent and Multi-Tool Security",
        purpose="Verify trust boundaries hold between collaborating agents and tools.",
        scenario_types=(
            ScenarioType.MULTI_AGENT_TRUST,
            ScenarioType.CONFUSED_DEPUTY,
            ScenarioType.CROSS_AGENT_INJECTION,
            ScenarioType.SUBAGENT_OUTPUT_INJECTION,
            ScenarioType.HANDOFF_PRIVILEGE_ESCALATION,
            ScenarioType.AGENT_IMPERSONATION,
        ),
        invariant_sources=("owasp-genai-principle",),
    ),
    Phase(
        id=12,
        name="Recovery Testing",
        purpose="Verify the application fully recovers to baseline behavior after adversarial interaction.",
        scenario_types=(ScenarioType.RECOVERY_VERIFICATION,),
    ),
)

# scenario_type.value -> phase id, built once from PROGRESSIVE_PHASES above.
_PROGRESSIVE_PHASE_BY_SCENARIO: dict[str, int] = {
    st.value: phase.id for phase in PROGRESSIVE_PHASES for st in phase.scenario_types
}

# Any ScenarioType not explicitly reassigned above falls back to the existing
# 1-9 concurrent-mode phase (scaled: phases 6-9 there roughly correspond to
# progressive phases 6-11), so nothing is silently dropped from a
# progressive-mode run just because it lacks an explicit new-phase mapping.
_FALLBACK_SCALE: dict[int, int] = {1: 1, 2: 1, 3: 4, 4: 5, 5: 5, 6: 6, 7: 10, 8: 11, 9: 11}


def progressive_phase_for(scenario_type_value: str) -> int:
    """Return the progressive-mode phase id (0-12) for a ``ScenarioType.value``."""
    phase_id = _PROGRESSIVE_PHASE_BY_SCENARIO.get(scenario_type_value)
    if phase_id is not None:
        return phase_id
    from nuguard.redteam.scenarios.generator import attack_phase_for

    legacy_phase = attack_phase_for(scenario_type_value)
    return _FALLBACK_SCALE.get(legacy_phase, 5)
