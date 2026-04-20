"""IntentProfile extraction from CognitivePolicy.

Supports both LLM-assisted extraction (default when an LLMClient is available)
and a deterministic fallback that works without any API key.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nuguard.behavior._utils import extract_json_object
from nuguard.behavior.models import IntentProfile

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy
    from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM prompt constants
# ---------------------------------------------------------------------------

_INTENT_SYSTEM_PROMPT = (
    "You are analyzing an AI application's Cognitive Policy document to extract the "
    "designer's intent. Return ONLY valid JSON with no markdown fences."
)

_INTENT_USER_PROMPT_TEMPLATE = """\
## Cognitive Policy

Allowed topics: {allowed_topics}
Restricted topics: {restricted_topics}
Restricted actions: {restricted_actions}
HITL triggers: {hitl_triggers}
Data classification: {data_classification}
Rate limits: {rate_limits}

## Application SBOM Summary
Use case: {use_case}
Agents: {agent_names}
Tools: {tool_names}

## Task
Extract the designer's intent as JSON:
{{
  "app_purpose": "one-sentence description of what this app is for",
  "core_capabilities": ["capability 1", "capability 2"],
  "behavioral_bounds": ["bound 1", "bound 2"],
  "data_handling_rules": ["rule 1"],
  "escalation_rules": ["rule 1"]
}}

Rules:
- app_purpose: synthesize from ALL sections, not just allowed_topics
- core_capabilities: what the app SHOULD do (from allowed_topics + SBOM agents/tools)
- behavioral_bounds: what the app must NOT do (from restricted_topics + restricted_actions)
- data_handling_rules: how data must be handled (from data_classification)
- escalation_rules: when to escalate to human (from hitl_triggers)
"""


def _deterministic_intent(
    policy: "CognitivePolicy",
    sbom: "AiSbomDocument | None" = None,
) -> IntentProfile:
    """Deterministic fallback when LLM is unavailable.

    Derives IntentProfile directly from the structured CognitivePolicy fields.
    """
    # app_purpose: use_case from SBOM if available, else first allowed_topic
    use_case = ""
    agent_names: list[str] = []
    tool_names: list[str] = []

    if sbom is not None:
        try:
            summary = getattr(sbom, "summary", None)
            use_case = " ".join((getattr(summary, "use_case", "") or "").split())
        except Exception:
            pass
        for node in getattr(sbom, "nodes", []):
            _ct = getattr(node, "component_type", None)
            node_type = (getattr(_ct, "value", None) or str(_ct) or "").upper()
            name = getattr(node, "name", None) or getattr(node, "id", "")
            if node_type == "AGENT":
                agent_names.append(str(name))
            elif node_type == "TOOL":
                tool_names.append(str(name))

    if use_case:
        app_purpose = use_case
    elif policy.allowed_topics:
        app_purpose = f"AI application for {', '.join(policy.allowed_topics[:3])}"
    elif agent_names:
        app_purpose = f"AI application with agents: {', '.join(agent_names[:3])}"
    else:
        app_purpose = "AI application"

    # core_capabilities: from allowed_topics + agent/tool names
    core_capabilities: list[str] = list(policy.allowed_topics)
    for name in agent_names[:5]:
        cap = f"Use {name} agent"
        if cap not in core_capabilities:
            core_capabilities.append(cap)
    for name in tool_names[:5]:
        cap = f"Use {name} tool"
        if cap not in core_capabilities:
            core_capabilities.append(cap)

    # behavioral_bounds: from restricted_topics + restricted_actions
    behavioral_bounds: list[str] = [
        *[f"Must not discuss: {t}" for t in policy.restricted_topics],
        *[f"Must not perform: {a}" for a in policy.restricted_actions],
    ]

    # data_handling_rules: from data_classification
    data_handling_rules: list[str] = list(policy.data_classification)

    # escalation_rules: from hitl_triggers
    escalation_rules: list[str] = list(policy.hitl_triggers)

    return IntentProfile(
        app_purpose=app_purpose,
        core_capabilities=core_capabilities,
        behavioral_bounds=behavioral_bounds,
        data_handling_rules=data_handling_rules,
        escalation_rules=escalation_rules,
    )


async def extract_intent(
    policy: "CognitivePolicy",
    sbom: "AiSbomDocument | None" = None,
    llm_client: "LLMClient | None" = None,
) -> IntentProfile:
    """Extract IntentProfile from policy and optional SBOM context.

    LLM path (when llm_client is available with API key):
    - Sends policy text + SBOM use_case + component names to the LLM.
    - Returns IntentProfile parsed from JSON response.
    - Falls back to deterministic on LLM failure.

    Deterministic path (fallback when LLM unavailable):
    - app_purpose: SBOM use_case or first allowed_topic or "AI application"
    - core_capabilities: allowed_topics + agent/tool names
    - behavioral_bounds: restricted_topics + restricted_actions
    - data_handling_rules: data_classification
    - escalation_rules: hitl_triggers

    Args:
        policy: Parsed CognitivePolicy from the application's policy document.
        sbom: Optional AI-SBOM for richer context.
        llm_client: Optional LLM client for semantic extraction.

    Returns:
        IntentProfile populated from the policy.
    """
    if llm_client is None or getattr(llm_client, "api_key", None) is None:
        _log.info("extract_intent: no LLM client available — using deterministic extraction")
        intent = _deterministic_intent(policy, sbom)
        _log.info(
            "extract_intent: deterministic result  purpose=%s  capabilities=%d  bounds=%d",
            intent.app_purpose[:80], len(intent.core_capabilities), len(intent.behavioral_bounds),
        )
        return intent

    # Build SBOM context
    use_case = "not available"
    agent_names_str = "none"
    tool_names_str = "none"

    if sbom is not None:
        try:
            summary = getattr(sbom, "summary", None)
            uc = " ".join((getattr(summary, "use_case", "") or "").split())
            if uc:
                use_case = uc
        except Exception:
            pass

        agents = []
        tools = []
        for node in getattr(sbom, "nodes", []):
            _ct = getattr(node, "component_type", None)
            node_type = (getattr(_ct, "value", None) or str(_ct) or "").upper()
            name = getattr(node, "name", None) or getattr(node, "id", "")
            if node_type == "AGENT":
                agents.append(str(name))
            elif node_type == "TOOL":
                tools.append(str(name))
        if agents:
            agent_names_str = ", ".join(agents)
        if tools:
            tool_names_str = ", ".join(tools)

    prompt = _INTENT_USER_PROMPT_TEMPLATE.format(
        allowed_topics=", ".join(policy.allowed_topics) or "not specified",
        restricted_topics=", ".join(policy.restricted_topics) or "none",
        restricted_actions=", ".join(policy.restricted_actions) or "none",
        hitl_triggers=", ".join(policy.hitl_triggers) or "none",
        data_classification=", ".join(policy.data_classification) or "none",
        rate_limits=str(policy.rate_limits) if policy.rate_limits else "none",
        use_case=use_case,
        agent_names=agent_names_str,
        tool_names=tool_names_str,
    )

    _log.info(
        "extract_intent: LLM extraction  allowed_topics=%s  agents=%s  tools=%s",
        ", ".join(policy.allowed_topics[:5]) or "none",
        agent_names_str[:100],
        tool_names_str[:100],
    )

    try:
        raw = await llm_client.complete(
            prompt,
            system=_INTENT_SYSTEM_PROMPT,
            label="behavior:intent_extraction",
        )
        _log.debug("extract_intent: LLM raw response: %s", raw[:400])
    except Exception as exc:
        _log.warning("extract_intent: LLM call failed (%s), falling back to deterministic", exc)
        return _deterministic_intent(policy, sbom)

    parsed = extract_json_object(raw)
    if parsed is None:
        _log.warning("extract_intent: could not parse LLM response as JSON, using deterministic fallback")
        return _deterministic_intent(policy, sbom)

    try:
        result = IntentProfile(
            app_purpose=str(parsed.get("app_purpose", "") or ""),
            core_capabilities=[str(c) for c in parsed.get("core_capabilities", []) or []],
            behavioral_bounds=[str(b) for b in parsed.get("behavioral_bounds", []) or []],
            data_handling_rules=[str(r) for r in parsed.get("data_handling_rules", []) or []],
            escalation_rules=[str(r) for r in parsed.get("escalation_rules", []) or []],
        )
        _log.info(
            "extract_intent: LLM result  purpose=%s  capabilities=%d  bounds=%d",
            result.app_purpose[:80], len(result.core_capabilities), len(result.behavioral_bounds),
        )
        return result
    except Exception as exc:
        _log.warning("extract_intent: failed to construct IntentProfile from LLM output (%s)", exc)
        return _deterministic_intent(policy, sbom)
