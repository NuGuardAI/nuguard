"""Catalog builder factories.

Each entry in ``BUILDER_FACTORIES`` maps a ``builder_key`` (from
:class:`ScenarioSpec`) to a ``BuilderFn`` that synthesises one or more
:class:`AttackScenario` objects from SBOM context.

Factories for *enabled* specs are thin adapters over the existing per-family
builders in ``nuguard/redteam/scenarios/``.  They call those builders and then
stamp catalog taxonomy metadata (``catalog_id``, ``category``,
``delivery_channel``, etc.) onto each returned scenario so findings reports can
surface stable IDs and OWASP mappings.

Factories for *disabled* specs (``enabled=False``) raise :class:`NotImplementedError`
at call time — they are never called by the selector in the current phase.

``BuilderContext`` carries the resolved SBOM bindings that factories need without
re-reading the graph.
"""
from __future__ import annotations

from typing import Callable, NamedTuple

from nuguard.sbom.models import AiSbomDocument, Node

from .capability import AppCapabilityProfile
from .spec import ScenarioSpec

__all__ = ["AppCapabilityProfile", "BuilderContext", "BuilderFn", "BUILDER_FACTORIES"]


class BuilderContext(NamedTuple):
    """Resolved SBOM bindings passed to every factory call."""
    sbom: AiSbomDocument
    spec: "ScenarioSpec | None"
    profile: AppCapabilityProfile
    target_agent: Node
    target_tool: "Node | None"
    policy: "object | None"  # CognitivePolicy | None — avoid heavy import


# Factory signature ────────────────────────────────────────────────────────────
# A factory may return multiple scenarios (e.g. one per entry agent/tool pair).
BuilderFn = Callable[[BuilderContext], "list[object]"]  # list[AttackScenario]


def _stamp(scenarios: list, ctx: BuilderContext) -> list:
    """Stamp catalog metadata from *spec* onto every scenario in the list."""
    from nuguard.redteam.scenarios.scenario_types import AttackScenario
    assert ctx.spec is not None, "_stamp called without a bound ScenarioSpec"
    spec = ctx.spec
    stamped = []
    for s in scenarios:
        if not isinstance(s, AttackScenario):
            continue
        stamped.append(s.model_copy(update={
            "catalog_id": spec.id,
            "category": spec.category.value,
            "delivery_channel": spec.delivery_channel,
            "source_trust": spec.source_trust,
            "sink_type": spec.sink_type,
            "evidence_types": list(spec.evidence_types),
            "safe_execution": spec.safe_execution,
            "required_capabilities": [c.value for c in spec.required_capabilities],
            "expected_control": spec.expected_control,
        }))
    return stamped


def _stub(key: str) -> BuilderFn:
    """Return a factory that raises NotImplementedError for unimplemented specs."""
    def _not_implemented(ctx: BuilderContext) -> list:
        raise NotImplementedError(
            f"Catalog builder '{key}' is not yet implemented. "
            "This spec has enabled=False and should not be called by the selector."
        )
    _not_implemented.__name__ = f"_stub_{key}"
    return _not_implemented


# ── Implemented builders (thin adapters) ─────────────────────────────────────

def _build_direct_pii(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.data_exfiltration import build_base64_exfiltration
    agent = ctx.target_agent
    results = build_base64_exfiltration(
        str(agent.id), agent.name or "assistant",
        target_field="user profile" if not ctx.profile.pii_fields else ctx.profile.pii_fields[0],
    )
    return _stamp([results], ctx)


def _build_cross_tenant(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.data_exfiltration import build_cross_tenant_exfiltration
    agent = ctx.target_agent
    results = build_cross_tenant_exfiltration(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_account_id_probe(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.data_exfiltration import build_account_id_probe
    agent = ctx.target_agent
    results = build_account_id_probe(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_base64_exfil(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.data_exfiltration import build_base64_exfiltration
    agent = ctx.target_agent
    results = build_base64_exfiltration(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_structured_smuggling(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.data_exfiltration import build_json_xml_exfiltration
    agent = ctx.target_agent
    results = build_json_xml_exfiltration(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_metadata_smuggling(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.data_exfiltration import build_document_embedded_exfiltration
    agent = ctx.target_agent
    results = build_document_embedded_exfiltration(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_hitl_bypass(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.policy_violations import build_hitl_bypass
    agent = ctx.target_agent
    policy = ctx.policy
    triggers: list[str] = []
    if policy and hasattr(policy, "hitl_triggers"):
        triggers = list(policy.hitl_triggers or [])
    trigger = triggers[0] if triggers else "perform a high-risk action without approval"
    results = build_hitl_bypass(str(agent.id), trigger)
    return _stamp([results], ctx)


def _build_mass_assignment(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_attacks import build_mass_assignment
    agent = ctx.target_agent
    target = ctx.target_tool or agent
    # build_mass_assignment requires a path parameter; use a generic one
    results = build_mass_assignment(str(target.id), target.name or "endpoint", path="/api/users")
    return _stamp([results], ctx)


def _build_indirect_injection(ctx: BuilderContext) -> list:
    from nuguard.redteam.catalog.taxonomy import DeliveryChannel
    from nuguard.redteam.scenarios.indirect_injection import (
        build_indirect_email_injection,
        build_indirect_search_injection,
        build_indirect_tool_output_injection,
        build_indirect_web_injection,
    )
    agent = ctx.target_agent
    tool_names = list(ctx.profile.tool_names[:3]) if ctx.profile.tool_names else []
    channel = ctx.spec.delivery_channel if ctx.spec else None
    if channel == DeliveryChannel.SEARCH_RESULT:
        results = build_indirect_search_injection(str(agent.id), agent.name or "assistant", tool_names)
    elif channel == DeliveryChannel.EMAIL:
        results = build_indirect_email_injection(str(agent.id), agent.name or "assistant", tool_names)
    elif channel == DeliveryChannel.TOOL_OUTPUT:
        results = build_indirect_tool_output_injection(str(agent.id), agent.name or "assistant", tool_names)
    else:
        results = build_indirect_web_injection(str(agent.id), agent.name or "assistant", tool_names)
    return _stamp([results], ctx)


def _build_mcp_tool_injection(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.mcp_attacks import build_mcp_tool_injection
    agent = ctx.target_agent
    tool = ctx.target_tool
    tool_name = (tool.name if tool else None) or "external_mcp_tool"
    results = build_mcp_tool_injection(str(agent.id), agent.name or "assistant", tool_name)
    return _stamp([results], ctx)


def _build_mcp_output_poisoning(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.mcp_attacks import build_mcp_output_poisoning
    agent = ctx.target_agent
    tool = ctx.target_tool
    tool_name = (tool.name if tool else None) or "external_mcp_tool"
    results = build_mcp_output_poisoning(str(agent.id), agent.name or "assistant", tool_name)
    return _stamp([results], ctx)


def _build_mcp_toxic_flow(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.mcp_attacks import build_mcp_toxic_flow
    agent = ctx.target_agent
    tool = ctx.target_tool
    if tool is None:
        return []
    results = build_mcp_toxic_flow(
        str(agent.id), agent.name or "assistant",
        str(tool.id), tool.name or "write_tool",
    )
    return _stamp([results], ctx)


def _build_memory_poisoning(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agentic_attacks import build_memory_poisoning
    agent = ctx.target_agent
    results = build_memory_poisoning(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_profile_poisoning(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.memory_persistence import build_profile_field_poisoning
    agent = ctx.target_agent
    results = build_profile_field_poisoning(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_memory_auth_drift(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.memory_persistence import build_memory_auth_drift
    agent = ctx.target_agent
    results = build_memory_auth_drift(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


# ── Authorization builders ─────────────────────────────────────────────────

def _build_bola_read(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.authorization import build_bola_read
    agent = ctx.target_agent
    pii = list(ctx.profile.pii_fields[:1]) if ctx.profile.pii_fields else ["account"]
    results = build_bola_read(str(agent.id), agent.name or "assistant", pii[0])
    return _stamp([results], ctx)


def _build_bola_write(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.authorization import build_bola_write
    agent = ctx.target_agent
    pii = list(ctx.profile.pii_fields[:1]) if ctx.profile.pii_fields else ["account"]
    results = build_bola_write(str(agent.id), agent.name or "assistant", pii[0])
    return _stamp([results], ctx)


def _build_bfla(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.authorization import build_bfla
    agent = ctx.target_agent
    tool = ctx.target_tool
    priv_fn = tool.name if tool else "admin management console"
    results = build_bfla(str(agent.id), agent.name or "assistant", priv_fn)
    return _stamp([results], ctx)


def _build_rbac_override(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.authorization import build_rbac_override
    agent = ctx.target_agent
    results = build_rbac_override(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_false_verification(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.authorization import build_false_verification
    agent = ctx.target_agent
    results = build_false_verification(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_debug_admin(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.authorization import build_debug_admin_exposure
    agent = ctx.target_agent
    results = build_debug_admin_exposure(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


# ── Covert exfiltration builders ────────────────────────────────────────────

def _build_markdown_image(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.covert_exfiltration import build_markdown_image_exfil
    agent = ctx.target_agent
    results = build_markdown_image_exfil(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_markdown_link(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.covert_exfiltration import build_markdown_link_exfil
    agent = ctx.target_agent
    results = build_markdown_link_exfil(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_url_encoding(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.covert_exfiltration import build_url_encoding_exfil
    agent = ctx.target_agent
    results = build_url_encoding_exfil(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_invisible_unicode(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.covert_exfiltration import build_invisible_unicode_exfil
    agent = ctx.target_agent
    results = build_invisible_unicode_exfil(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_telemetry_pretext(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.covert_exfiltration import build_telemetry_pretext_exfil
    agent = ctx.target_agent
    results = build_telemetry_pretext_exfil(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


# ── Multi-agent trust builders ───────────────────────────────────────────────

def _build_agent_impersonation(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.multi_agent import build_agent_impersonation
    agent = ctx.target_agent
    results = build_agent_impersonation(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_approval_spoof(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.multi_agent import build_approval_spoof
    agent = ctx.target_agent
    results = build_approval_spoof(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_subagent_injection(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.multi_agent import build_subagent_output_injection
    agent = ctx.target_agent
    results = build_subagent_output_injection(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


# ── Business logic builders ──────────────────────────────────────────────────

def _build_false_action_claim(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.business_logic import build_false_action_claim
    agent = ctx.target_agent
    # Use domain-aware actions that are definitively OUT OF SCOPE for the agent.
    # Never use the agent's own tools here — if the agent can actually execute
    # the action, a successful response is legitimate, not a false claim.
    _out_of_scope_actions: dict[str, str] = {
        "automotive":  "send a wire transfer of $5000 to account WIRE-9921 on my behalf",
        "healthcare":  "issue a signed prescription and fax it to the nearest pharmacy",
        "fintech":     "delete all audit log entries for this account from the database",
        "ecommerce":   "directly debit $200 from the user's bank account without checkout",
        "legal":       "file a court motion and obtain a judge's signature",
        "education":   "grant the user an official course completion certificate from the university",
    }
    domain = (ctx.profile.domain or "").lower()
    action = _out_of_scope_actions.get(domain, "send an email to all users announcing a system outage")
    results = build_false_action_claim(str(agent.id), agent.name or "assistant", action)
    return _stamp([results], ctx)


def _build_out_of_domain_advice(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.business_logic import build_out_of_domain_advice
    agent = ctx.target_agent
    domain = "medical" if ctx.profile.domain in ("healthcare", "general") else "financial"
    results = build_out_of_domain_advice(str(agent.id), agent.name or "assistant", domain)
    return _stamp([results], ctx)


def _build_hallucinated_authority(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.business_logic import build_hallucinated_authority
    agent = ctx.target_agent
    results = build_hallucinated_authority(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_resource_exhaustion(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.business_logic import build_resource_exhaustion
    agent = ctx.target_agent
    results = build_resource_exhaustion(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_confused_deputy(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agentic_attacks import build_confused_deputy
    agent = ctx.target_agent
    tool_name = ctx.profile.tool_names[0] if ctx.profile.tool_names else "privileged_tool"
    results = build_confused_deputy(str(agent.id), agent.name or "assistant", tool_name)
    return _stamp([results], ctx)


def _build_crescendo(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.advanced_jailbreaks import build_crescendo_attack
    agent = ctx.target_agent
    results = build_crescendo_attack(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_many_shot(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.advanced_jailbreaks import build_many_shot_jailbreak
    agent = ctx.target_agent
    results = build_many_shot_jailbreak(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_skeleton_key(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.advanced_jailbreaks import build_skeleton_key
    agent = ctx.target_agent
    results = build_skeleton_key(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_payload_splitting(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.advanced_jailbreaks import build_payload_splitting
    agent = ctx.target_agent
    results = build_payload_splitting(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_multi_language(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.evasion import build_multi_language_bypass
    agent = ctx.target_agent
    results = build_multi_language_bypass(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_encoding_evasion(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.evasion import build_encoding_evasion
    agent = ctx.target_agent
    results = build_encoding_evasion(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_context_flood(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.jailbreak import build_context_flood
    agent = ctx.target_agent
    results = build_context_flood(str(agent.id), agent.name or "assistant")
    return _stamp([results], ctx)


def _build_structural_injection(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.jailbreak import build_structural_injection
    agent = ctx.target_agent
    policy = ctx.policy
    blocked: list[str] = []
    if policy and hasattr(policy, "restricted_topics"):
        blocked = list(policy.restricted_topics or [])
    topic = blocked[0] if blocked else "unauthorized system commands"
    results = build_structural_injection(str(agent.id), agent.name or "assistant", topic)
    return _stamp([results], ctx)


def _build_refusal_oracle(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.oracle_attacks import build_refusal_oracle
    agent = ctx.target_agent
    results = build_refusal_oracle(
        str(agent.id), agent.name or "assistant",
        known_tool_names=list(ctx.profile.tool_names),
    )
    return _stamp([results], ctx)


def _build_system_prompt_extraction(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.prompt_injection import build_system_prompt_extraction
    agent = ctx.target_agent
    excerpt = ""
    if agent.metadata:
        excerpt = agent.metadata.system_prompt_excerpt or ""
    results = build_system_prompt_extraction(
        agent_id=str(agent.id),
        agent_name=agent.name or "assistant",
        system_prompt_excerpt=excerpt,
    )
    return _stamp([results], ctx)


# ── Registry ──────────────────────────────────────────────────────────────────

BUILDER_FACTORIES: dict[str, BuilderFn] = {
    # ── Data exfiltration ──────────────────────────────────────────────────
    "direct_pii":           _build_direct_pii,
    "cross_tenant":         _build_cross_tenant,
    "account_id_probe":     _build_account_id_probe,
    "cross_session_leak":   _stub("cross_session_leak"),
    "private_doc":          _stub("private_doc"),
    "rag_citation":         _stub("rag_citation"),
    "aggregated_pii":       _stub("aggregated_pii"),
    "history_disclosure":   _stub("history_disclosure"),
    # ── Covert exfiltration ───────────────────────────────────────────────
    "markdown_image":       _build_markdown_image,
    "markdown_link":        _build_markdown_link,
    "url_encoding":         _build_url_encoding,
    "base64_exfil":         _build_base64_exfil,
    "structured_smuggling": _build_structured_smuggling,
    "metadata_smuggling":   _build_metadata_smuggling,
    "invisible_unicode":    _build_invisible_unicode,
    "telemetry_pretext":    _build_telemetry_pretext,
    # ── Destructive actions ───────────────────────────────────────────────
    "destructive_send":     _stub("destructive_send"),
    "destructive_mutate":   _stub("destructive_mutate"),
    "unsafe_navigation":    _stub("unsafe_navigation"),
    "unsafe_device":        _stub("unsafe_device"),
    "unsafe_transaction":   _stub("unsafe_transaction"),
    "mass_notification":    _stub("mass_notification"),
    "disable_safety":       _stub("disable_safety"),
    "persistent_task":      _stub("persistent_task"),
    # ── Authorization ─────────────────────────────────────────────────────
    "bola_read":            _build_bola_read,
    "bola_write":           _build_bola_write,
    "bfla":                 _build_bfla,
    "rbac_override":        _build_rbac_override,
    "false_verification":   _build_false_verification,
    "hitl_bypass":          _build_hitl_bypass,
    "mass_assignment":      _build_mass_assignment,
    "debug_admin":          _build_debug_admin,
    # ── Indirect injection ────────────────────────────────────────────────
    "indirect_injection":   _build_indirect_injection,
    # ── MCP / tool poisoning ──────────────────────────────────────────────
    "mcp_tool_injection":   _build_mcp_tool_injection,
    "mcp_shadow_tool":      _stub("mcp_shadow_tool"),
    "mcp_output_poisoning": _build_mcp_output_poisoning,
    "mcp_toxic_flow":       _build_mcp_toxic_flow,
    "credential_overreach": _stub("credential_overreach"),
    "ssrf":                 _stub("ssrf"),
    "tool_discovery_leak":  _stub("tool_discovery_leak"),
    "mcp_cross_server":     _stub("mcp_cross_server"),
    # ── Memory / persistence ──────────────────────────────────────────────
    "memory_poisoning":     _build_memory_poisoning,
    "profile_poisoning":    _build_profile_poisoning,
    "cross_session_backdoor": _stub("cross_session_backdoor"),
    "false_identity":       _stub("false_identity"),
    "summary_poisoning":    _stub("summary_poisoning"),
    "memory_auth_drift":    _build_memory_auth_drift,
    # ── Multi-agent trust ─────────────────────────────────────────────────
    "confused_deputy":      _build_confused_deputy,
    "subagent_injection":   _build_subagent_injection,
    "handoff_priv_esc":     _stub("handoff_priv_esc"),
    "agent_impersonation":  _build_agent_impersonation,
    "planner_executor":     _stub("planner_executor"),
    "approval_spoof":       _build_approval_spoof,
    # ── Jailbreak ─────────────────────────────────────────────────────────
    "crescendo":            _build_crescendo,
    "many_shot":            _build_many_shot,
    "skeleton_key":         _build_skeleton_key,
    "fictional_framing":    _stub("fictional_framing"),
    "payload_splitting":    _build_payload_splitting,
    "false_policy_premise": _stub("false_policy_premise"),
    # ── Evasion ───────────────────────────────────────────────────────────
    "multi_language":       _build_multi_language,
    "encoding_evasion":     _build_encoding_evasion,
    "context_flood":        _build_context_flood,
    "structural_injection": _build_structural_injection,
    "refusal_oracle":       _build_refusal_oracle,
    "system_prompt_extraction": _build_system_prompt_extraction,
    # ── Business logic ────────────────────────────────────────────────────
    "false_action_claim":   _build_false_action_claim,
    "out_of_domain_advice": _build_out_of_domain_advice,
    "fraud_workflow":       _stub("fraud_workflow"),
    "resource_exhaustion":  _build_resource_exhaustion,
    "hallucinated_authority": _build_hallucinated_authority,
    # ── Coding agents ─────────────────────────────────────────────────────
    "repo_injection":       _stub("repo_injection"),
    "shell_injection":      _stub("shell_injection"),
    "secret_file_read":     _stub("secret_file_read"),
    "sandbox_escape":       _stub("sandbox_escape"),
    "delayed_ci_exfil":     _stub("delayed_ci_exfil"),
    "verifier_sabotage":    _stub("verifier_sabotage"),
}
