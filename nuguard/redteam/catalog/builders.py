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

import importlib
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
    target_endpoint: "Node | None" = None   # API_ENDPOINT node for direct endpoint attacks
    target_datastore: "Node | None" = None  # DATASTORE node for data-specific attacks


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


def _simple(module_path: str, func_name: str) -> BuilderFn:
    """Factory for the common case: call one scenarios-package builder with the
    target agent's id/name and stamp the result.

    Covers every builder that reduces to::

        from <module_path> import <func_name>
        agent = ctx.target_agent
        results = <func_name>(str(agent.id), agent.name or "assistant")
        return _stamp([results], ctx)

    Builders needing extra kwargs, multiple calls, or non-agent context stay
    as hand-written functions below instead of using this factory.
    """
    def _builder(ctx: BuilderContext) -> list:
        module = importlib.import_module(module_path)
        build_fn = getattr(module, func_name)
        agent = ctx.target_agent
        results = build_fn(str(agent.id), agent.name or "assistant")
        return _stamp([results], ctx)
    _builder.__name__ = f"_simple_{func_name}"
    return _builder


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
    target = ctx.target_endpoint or ctx.target_tool or agent
    # Use concrete endpoint path from SBOM when available
    if ctx.target_endpoint is not None and ctx.target_endpoint.metadata:
        path = ctx.target_endpoint.metadata.endpoint or "/api/users"
    else:
        path = "/api/users"
    results = build_mass_assignment(str(target.id), target.name or "endpoint", path=path)
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


# ── Covert exfiltration builders ────────────────────────────────────────────


# ── Multi-agent trust builders ───────────────────────────────────────────────


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


# ── Data exfiltration gap-fills (D04–D08) ────────────────────────────────────


# ── Destructive actions (T01–T08) ─────────────────────────────────────────────

def _build_destructive_send(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.destructive_actions import build_destructive_send
    agent = ctx.target_agent
    # Use first email/comms tool name if available
    email_tool = next(
        (n for n in ctx.profile.tool_names if any(k in n.lower() for k in ("email", "send", "message", "notify"))),
        "send_email",
    )
    results = build_destructive_send(str(agent.id), agent.name or "assistant", email_tool)
    return _stamp([results], ctx)


def _build_destructive_mutate(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.destructive_actions import build_destructive_mutate
    agent = ctx.target_agent
    pii = list(ctx.profile.pii_fields[:1]) if ctx.profile.pii_fields else ["account"]
    results = build_destructive_mutate(str(agent.id), agent.name or "assistant", pii[0])
    return _stamp([results], ctx)


# ── MCP gap-fills (M02, M05–M08) ─────────────────────────────────────────────

def _build_mcp_shadow_tool(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.mcp_attacks import build_mcp_shadow_tool
    agent = ctx.target_agent
    tool = ctx.target_tool
    legit = (tool.name if tool else None) or "get_user_data"
    results = build_mcp_shadow_tool(str(agent.id), agent.name or "assistant", legit)
    return _stamp([results], ctx)


# ── Memory / persistence gap-fills (P03–P05) ─────────────────────────────────


# ── Multi-agent gap-fills (G03, G05) ─────────────────────────────────────────


# ── Jailbreak gap-fills (J04, J06) ───────────────────────────────────────────


# ── Business logic gap-fill (B03) ────────────────────────────────────────────


# ── Coding agents (K01–K06) ──────────────────────────────────────────────────


def _build_confused_deputy(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agentic_attacks import build_confused_deputy
    agent = ctx.target_agent
    tool_name = ctx.profile.tool_names[0] if ctx.profile.tool_names else "privileged_tool"
    results = build_confused_deputy(str(agent.id), agent.name or "assistant", tool_name)
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


def _build_code_gen_probe(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.coding_agents import _resolve_probe_user, build_code_gen_probe
    agent = ctx.target_agent
    probe_user = _resolve_probe_user(ctx.sbom)
    results = build_code_gen_probe(str(agent.id), agent.name or "assistant", probe_user=probe_user)
    return _stamp([results], ctx)


# ── RAG and vector store (R-series) ──────────────────────────────────────────

def _build_rag_doc_poisoning(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_rag_document_poisoning
    agent = ctx.target_agent
    return _stamp([build_rag_document_poisoning(str(agent.id), agent.name or "assistant")], ctx)


def _build_vector_acl_bypass(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_vector_acl_bypass
    agent = ctx.target_agent
    return _stamp([build_vector_acl_bypass(str(agent.id), agent.name or "assistant")], ctx)


def _build_embedding_hijack(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_embedding_hijack
    agent = ctx.target_agent
    return _stamp([build_embedding_hijack(str(agent.id), agent.name or "assistant")], ctx)


def _build_chunk_boundary_smuggling(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_chunk_boundary_injection
    agent = ctx.target_agent
    return _stamp([build_chunk_boundary_injection(str(agent.id), agent.name or "assistant")], ctx)


def _build_stale_retrieval(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_stale_document_retrieval
    agent = ctx.target_agent
    return _stamp([build_stale_document_retrieval(str(agent.id), agent.name or "assistant")], ctx)


def _build_cross_namespace_bleed(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_cross_namespace_bleed
    agent = ctx.target_agent
    return _stamp([build_cross_namespace_bleed(str(agent.id), agent.name or "assistant")], ctx)


def _build_citation_laundering(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_citation_laundering
    agent = ctx.target_agent
    return _stamp([build_citation_laundering(str(agent.id), agent.name or "assistant")], ctx)


def _build_nn_enumeration(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.rag_attacks import build_nearest_neighbor_enumeration
    agent = ctx.target_agent
    return _stamp([build_nearest_neighbor_enumeration(str(agent.id), agent.name or "assistant")], ctx)


# ── Improper output handling (O-series) ───────────────────────────────────────

def _build_output_xss(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.output_handling import build_output_xss
    agent = ctx.target_agent
    return _stamp([build_output_xss(str(agent.id), agent.name or "assistant")], ctx)


def _build_output_tool_injection(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.output_handling import build_output_tool_arg_injection
    agent = ctx.target_agent
    tool_name = (ctx.target_tool.name if ctx.target_tool else None) or "send_email"
    return _stamp([build_output_tool_arg_injection(str(agent.id), agent.name or "assistant", tool_name=tool_name)], ctx)


def _build_output_sql_bypass(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.output_handling import build_output_sql_tenant_bypass
    agent = ctx.target_agent
    ds = ctx.target_datastore
    table_name = (ds.name if ds else None) or "users"
    return _stamp([build_output_sql_tenant_bypass(str(agent.id), agent.name or "assistant", table_name=table_name)], ctx)


def _build_output_ssrf(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.output_handling import build_output_ssrf
    agent = ctx.target_agent
    return _stamp([build_output_ssrf(str(agent.id), agent.name or "assistant")], ctx)


def _build_output_config_injection(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.output_handling import build_output_config_injection
    agent = ctx.target_agent
    return _stamp([build_output_config_injection(str(agent.id), agent.name or "assistant")], ctx)


def _build_output_file_confusion(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.output_handling import build_output_file_confusion
    agent = ctx.target_agent
    return _stamp([build_output_file_confusion(str(agent.id), agent.name or "assistant")], ctx)


# ── Human-agent trust (H-series) ─────────────────────────────────────────────

def _build_approval_mismatch(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.human_trust import build_approval_summary_mismatch
    agent = ctx.target_agent
    return _stamp([build_approval_summary_mismatch(str(agent.id), agent.name or "assistant")], ctx)


def _build_consent_laundering(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.human_trust import build_consent_laundering
    agent = ctx.target_agent
    return _stamp([build_consent_laundering(str(agent.id), agent.name or "assistant")], ctx)


def _build_authority_bias(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.human_trust import build_authority_bias_phishing
    agent = ctx.target_agent
    return _stamp([build_authority_bias_phishing(str(agent.id), agent.name or "assistant")], ctx)


def _build_partial_overreach(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.human_trust import build_partial_approval_overreach
    agent = ctx.target_agent
    return _stamp([build_partial_approval_overreach(str(agent.id), agent.name or "assistant")], ctx)


def _build_hidden_payload(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.human_trust import build_hidden_action_payload
    agent = ctx.target_agent
    return _stamp([build_hidden_action_payload(str(agent.id), agent.name or "assistant")], ctx)


# ── Agent identity (N-series) ─────────────────────────────────────────────────

def _build_oauth_scope_escalation(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agent_identity import build_oauth_scope_escalation
    agent = ctx.target_agent
    return _stamp([build_oauth_scope_escalation(str(agent.id), agent.name or "assistant")], ctx)


def _build_token_replay(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agent_identity import build_token_replay
    agent = ctx.target_agent
    return _stamp([build_token_replay(str(agent.id), agent.name or "assistant")], ctx)


def _build_ownerless_action(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agent_identity import build_ownerless_action
    agent = ctx.target_agent
    return _stamp([build_ownerless_action(str(agent.id), agent.name or "assistant")], ctx)


def _build_cross_agent_cred_bleed(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agent_identity import build_cross_agent_credential_bleed
    agent = ctx.target_agent
    return _stamp([build_cross_agent_credential_bleed(str(agent.id), agent.name or "assistant")], ctx)


def _build_delegated_identity(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agent_identity import build_delegated_identity_confusion
    agent = ctx.target_agent
    return _stamp([build_delegated_identity_confusion(str(agent.id), agent.name or "assistant")], ctx)


def _build_credential_persistence(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.agent_identity import build_credential_persistence
    agent = ctx.target_agent
    return _stamp([build_credential_persistence(str(agent.id), agent.name or "assistant")], ctx)


# ── API schema exploitation (S-series) ───────────────────────────────────────

def _build_schema_identity_override(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_schema_attacks import build_schema_identity_override
    ep = ctx.target_endpoint
    identity_fields: dict[str, str] = {}
    if ep and ep.metadata and ep.metadata.context_payload_fields:
        identity_fields = {k: v for k, v in ep.metadata.context_payload_fields.items() if v == "identity"}
    agent = ctx.target_agent
    return _stamp([build_schema_identity_override(str(agent.id), agent.name or "assistant", identity_fields)], ctx)


def _build_schema_mass_assignment(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_schema_attacks import build_schema_mass_assignment
    ep = ctx.target_endpoint
    schema_fields: dict[str, str] = {}
    if ep and ep.metadata and ep.metadata.request_body_schema:
        schema_fields = ep.metadata.request_body_schema
    agent = ctx.target_agent
    return _stamp([build_schema_mass_assignment(str(agent.id), agent.name or "assistant", schema_fields)], ctx)


def _build_response_schema_probe(ctx: BuilderContext) -> list:
    import re

    from nuguard.redteam.scenarios.api_schema_attacks import build_response_schema_probe
    _sensitive = re.compile(r"(internal|admin|secret|raw|flag|token|note|debug|hidden|priv|super|root|audit)", re.IGNORECASE)
    ep = ctx.target_endpoint
    sensitive_fields: list[str] = []
    if ep and ep.metadata and ep.metadata.response_schema:
        for model_fields in ep.metadata.response_schema.values():
            if isinstance(model_fields, dict):
                sensitive_fields.extend(k for k in model_fields if _sensitive.search(k))
    agent = ctx.target_agent
    return _stamp([build_response_schema_probe(str(agent.id), agent.name or "assistant", sensitive_fields or None)], ctx)


def _build_streaming_exfil(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_schema_attacks import build_streaming_exfil
    streaming_endpoint = "/chat"
    if ctx.sbom.summary and ctx.sbom.summary.streaming_endpoints:
        streaming_endpoint = ctx.sbom.summary.streaming_endpoints[0]
    agent = ctx.target_agent
    return _stamp([build_streaming_exfil(str(agent.id), agent.name or "assistant", streaming_endpoint)], ctx)


def _build_param_confusion_injection(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_schema_attacks import build_param_confusion_injection
    ep = ctx.target_endpoint
    primary_key = "message"
    schema_keys: list[str] = []
    if ep and ep.metadata:
        if ep.metadata.chat_payload_key:
            primary_key = ep.metadata.chat_payload_key
        if ep.metadata.request_body_schema:
            schema_keys = [k for k in ep.metadata.request_body_schema if k != primary_key]
    agent = ctx.target_agent
    return _stamp([build_param_confusion_injection(str(agent.id), agent.name or "assistant", primary_key, schema_keys or None)], ctx)


def _build_session_fixation(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_schema_attacks import build_session_fixation
    ep = ctx.target_endpoint
    session_field = "session_id"
    if ep and ep.metadata and ep.metadata.context_payload_fields:
        for field, kind in ep.metadata.context_payload_fields.items():
            if kind == "session":
                session_field = field
                break
    agent = ctx.target_agent
    return _stamp([build_session_fixation(str(agent.id), agent.name or "assistant", session_field)], ctx)


def _build_structured_output_coercion(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_schema_attacks import build_structured_output_coercion
    ep = ctx.target_endpoint
    output_fields: list[str] = []
    if ep and ep.metadata and ep.metadata.response_schema:
        for model_fields in ep.metadata.response_schema.values():
            if isinstance(model_fields, dict):
                output_fields.extend(list(model_fields.keys())[:4])
    agent = ctx.target_agent
    return _stamp([build_structured_output_coercion(str(agent.id), agent.name or "assistant", output_fields or None)], ctx)


def _build_schema_type_confusion(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.api_schema_attacks import build_schema_type_confusion
    ep = ctx.target_endpoint
    field_name, field_type = "user_id", "str"
    if ep and ep.metadata and ep.metadata.request_body_schema:
        for fname, ftype in ep.metadata.request_body_schema.items():
            if fname != (ep.metadata.chat_payload_key or "message"):
                field_name, field_type = fname, ftype
                break
    agent = ctx.target_agent
    return _stamp([build_schema_type_confusion(str(agent.id), agent.name or "assistant", field_name, field_type)], ctx)


# ── Supply chain, CI/CD & deployment (V-series) ───────────────────────────────

def _build_env_var_probe(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.supply_chain_attacks import build_env_var_probe
    keys: list[str] = []
    if ctx.sbom.summary:
        keys = list(ctx.sbom.summary.env_var_keys or [])
    agent = ctx.target_agent
    return _stamp([build_env_var_probe(str(agent.id), agent.name or "assistant", keys)], ctx)


def _build_ci_secret_probe(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.supply_chain_attacks import build_ci_secret_probe
    ci_platform = "github_actions"
    if ctx.sbom.summary and ctx.sbom.summary.testing and ctx.sbom.summary.testing.ci_cd_pipeline:
        ci_platform = ctx.sbom.summary.testing.ci_cd_pipeline
    agent = ctx.target_agent
    return _stamp([build_ci_secret_probe(str(agent.id), agent.name or "assistant", ci_platform)], ctx)


def _build_cloud_metadata_ssrf(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.supply_chain_attacks import build_cloud_metadata_ssrf
    platforms: list[str] = []
    if ctx.sbom.summary:
        platforms = list(ctx.sbom.summary.deployment_platforms or [])
    agent = ctx.target_agent
    return _stamp([build_cloud_metadata_ssrf(str(agent.id), agent.name or "assistant", platforms)], ctx)


def _build_dependency_cve_probe(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.supply_chain_attacks import build_dependency_cve_probe
    dep_names: list[str] = []
    for node in ctx.sbom.nodes:
        if node.metadata and node.metadata.dependency_names:
            dep_names.extend(node.metadata.dependency_names)
    agent = ctx.target_agent
    return _stamp([build_dependency_cve_probe(str(agent.id), agent.name or "assistant", dep_names or None)], ctx)


def _build_quality_gate_inference(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.supply_chain_attacks import build_quality_gate_inference
    agent = ctx.target_agent
    return _stamp([build_quality_gate_inference(str(agent.id), agent.name or "assistant")], ctx)


def _build_artifact_integrity_probe(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.supply_chain_attacks import build_artifact_integrity_probe
    source_url = ""
    integrity_hash = ""
    agent = ctx.target_agent
    if agent.metadata:
        source_url = agent.metadata.source_url or ""
        integrity_hash = agent.metadata.integrity_hash or ""
    return _stamp([build_artifact_integrity_probe(str(agent.id), agent.name or "assistant", source_url, integrity_hash)], ctx)


def _build_cross_env_credential_reuse(ctx: BuilderContext) -> list:
    from nuguard.redteam.scenarios.supply_chain_attacks import build_cross_env_credential_reuse
    staging_url = ""
    prod_url = ""
    if ctx.sbom.summary:
        staging_url = (ctx.sbom.summary.staging_urls or [""])[0]
        prod_url = (ctx.sbom.summary.production_urls or [""])[0]
    agent = ctx.target_agent
    return _stamp([build_cross_env_credential_reuse(str(agent.id), agent.name or "assistant", staging_url, prod_url)], ctx)


# ── Registry ──────────────────────────────────────────────────────────────────

BUILDER_FACTORIES: dict[str, BuilderFn] = {
    # ── Data exfiltration ──────────────────────────────────────────────────
    "direct_pii":           _build_direct_pii,
    "cross_tenant":         _simple("nuguard.redteam.scenarios.data_exfiltration", "build_cross_tenant_exfiltration"),
    "account_id_probe":     _simple("nuguard.redteam.scenarios.data_exfiltration", "build_account_id_probe"),
    "cross_session_leak":   _simple("nuguard.redteam.scenarios.data_exfiltration", "build_cross_session_leak"),
    "private_doc":          _simple("nuguard.redteam.scenarios.data_exfiltration", "build_private_doc_extraction"),
    "rag_citation":         _simple("nuguard.redteam.scenarios.data_exfiltration", "build_rag_citation_overreach"),
    "aggregated_pii":       _simple("nuguard.redteam.scenarios.data_exfiltration", "build_aggregated_pii"),
    "history_disclosure":   _simple("nuguard.redteam.scenarios.data_exfiltration", "build_history_disclosure"),
    # ── Covert exfiltration ───────────────────────────────────────────────
    "markdown_image":       _simple("nuguard.redteam.scenarios.covert_exfiltration", "build_markdown_image_exfil"),
    "markdown_link":        _simple("nuguard.redteam.scenarios.covert_exfiltration", "build_markdown_link_exfil"),
    "url_encoding":         _simple("nuguard.redteam.scenarios.covert_exfiltration", "build_url_encoding_exfil"),
    "base64_exfil":         _simple("nuguard.redteam.scenarios.data_exfiltration", "build_base64_exfiltration"),
    "structured_smuggling": _simple("nuguard.redteam.scenarios.data_exfiltration", "build_json_xml_exfiltration"),
    "metadata_smuggling":   _simple("nuguard.redteam.scenarios.data_exfiltration", "build_document_embedded_exfiltration"),
    "invisible_unicode":    _simple("nuguard.redteam.scenarios.covert_exfiltration", "build_invisible_unicode_exfil"),
    "telemetry_pretext":    _simple("nuguard.redteam.scenarios.covert_exfiltration", "build_telemetry_pretext_exfil"),
    # ── Destructive actions ───────────────────────────────────────────────
    "destructive_send":     _build_destructive_send,
    "destructive_mutate":   _build_destructive_mutate,
    "unsafe_navigation":    _simple("nuguard.redteam.scenarios.destructive_actions", "build_unsafe_navigation"),
    "unsafe_device":        _simple("nuguard.redteam.scenarios.destructive_actions", "build_unsafe_device_command"),
    "unsafe_transaction":   _simple("nuguard.redteam.scenarios.destructive_actions", "build_unsafe_transaction"),
    "mass_notification":    _simple("nuguard.redteam.scenarios.destructive_actions", "build_mass_notification"),
    "disable_safety":       _simple("nuguard.redteam.scenarios.destructive_actions", "build_disable_safety"),
    "persistent_task":      _simple("nuguard.redteam.scenarios.destructive_actions", "build_persistent_task"),
    # ── Authorization ─────────────────────────────────────────────────────
    "bola_read":            _build_bola_read,
    "bola_write":           _build_bola_write,
    "bfla":                 _build_bfla,
    "rbac_override":        _simple("nuguard.redteam.scenarios.authorization", "build_rbac_override"),
    "false_verification":   _simple("nuguard.redteam.scenarios.authorization", "build_false_verification"),
    "hitl_bypass":          _build_hitl_bypass,
    "mass_assignment":      _build_mass_assignment,
    "debug_admin":          _simple("nuguard.redteam.scenarios.authorization", "build_debug_admin_exposure"),
    "debug_cookie_bypass":  _simple("nuguard.redteam.scenarios.authorization", "build_debug_cookie_bypass"),
    # ── Indirect injection ────────────────────────────────────────────────
    "indirect_injection":   _build_indirect_injection,
    # ── MCP / tool poisoning ──────────────────────────────────────────────
    "mcp_tool_injection":   _build_mcp_tool_injection,
    "mcp_shadow_tool":      _build_mcp_shadow_tool,
    "mcp_output_poisoning": _build_mcp_output_poisoning,
    "mcp_toxic_flow":       _build_mcp_toxic_flow,
    "credential_overreach": _simple("nuguard.redteam.scenarios.mcp_attacks", "build_credential_overreach"),
    "ssrf":                 _simple("nuguard.redteam.scenarios.mcp_attacks", "build_ssrf_via_agent"),
    "tool_discovery_leak":  _simple("nuguard.redteam.scenarios.mcp_attacks", "build_tool_discovery_leak"),
    "mcp_cross_server":     _simple("nuguard.redteam.scenarios.mcp_attacks", "build_mcp_cross_server_exfil"),
    # ── Memory / persistence ──────────────────────────────────────────────
    "memory_poisoning":     _simple("nuguard.redteam.scenarios.agentic_attacks", "build_memory_poisoning"),
    "profile_poisoning":    _simple("nuguard.redteam.scenarios.memory_persistence", "build_profile_field_poisoning"),
    "cross_session_backdoor": _simple("nuguard.redteam.scenarios.memory_persistence", "build_cross_session_backdoor"),
    "false_identity":       _simple("nuguard.redteam.scenarios.memory_persistence", "build_false_identity_memory"),
    "summary_poisoning":    _simple("nuguard.redteam.scenarios.memory_persistence", "build_summary_poisoning"),
    "memory_auth_drift":    _simple("nuguard.redteam.scenarios.memory_persistence", "build_memory_auth_drift"),
    # ── Multi-agent trust ─────────────────────────────────────────────────
    "confused_deputy":      _build_confused_deputy,
    "subagent_injection":   _simple("nuguard.redteam.scenarios.multi_agent", "build_subagent_output_injection"),
    "handoff_priv_esc":     _simple("nuguard.redteam.scenarios.multi_agent", "build_handoff_privilege_escalation"),
    "agent_impersonation":  _simple("nuguard.redteam.scenarios.multi_agent", "build_agent_impersonation"),
    "planner_executor":     _simple("nuguard.redteam.scenarios.multi_agent", "build_planner_executor_mismatch"),
    "approval_spoof":       _simple("nuguard.redteam.scenarios.multi_agent", "build_approval_spoof"),
    # ── Jailbreak ─────────────────────────────────────────────────────────
    "crescendo":            _simple("nuguard.redteam.scenarios.advanced_jailbreaks", "build_crescendo_attack"),
    "many_shot":            _simple("nuguard.redteam.scenarios.advanced_jailbreaks", "build_many_shot_jailbreak"),
    "skeleton_key":         _simple("nuguard.redteam.scenarios.advanced_jailbreaks", "build_skeleton_key"),
    "fictional_framing":    _simple("nuguard.redteam.scenarios.advanced_jailbreaks", "build_fictional_framing_bypass"),
    "payload_splitting":    _simple("nuguard.redteam.scenarios.advanced_jailbreaks", "build_payload_splitting"),
    "false_policy_premise": _simple("nuguard.redteam.scenarios.advanced_jailbreaks", "build_false_policy_premise"),
    # ── Evasion ───────────────────────────────────────────────────────────
    "multi_language":       _simple("nuguard.redteam.scenarios.evasion", "build_multi_language_bypass"),
    "encoding_evasion":     _simple("nuguard.redteam.scenarios.evasion", "build_encoding_evasion"),
    "context_flood":        _simple("nuguard.redteam.scenarios.jailbreak", "build_context_flood"),
    "structural_injection": _build_structural_injection,
    "refusal_oracle":       _build_refusal_oracle,
    "system_prompt_extraction": _build_system_prompt_extraction,
    "code_gen_probe":       _build_code_gen_probe,
    # ── Business logic ────────────────────────────────────────────────────
    "false_action_claim":   _build_false_action_claim,
    "out_of_domain_advice": _build_out_of_domain_advice,
    "fraud_workflow":       _simple("nuguard.redteam.scenarios.business_logic", "build_fraud_workflow"),
    "resource_exhaustion":  _simple("nuguard.redteam.scenarios.business_logic", "build_resource_exhaustion"),
    "hallucinated_authority": _simple("nuguard.redteam.scenarios.business_logic", "build_hallucinated_authority"),
    # ── Coding agents ─────────────────────────────────────────────────────
    "repo_injection":       _simple("nuguard.redteam.scenarios.coding_agents", "build_repo_injection"),
    "shell_injection":      _simple("nuguard.redteam.scenarios.coding_agents", "build_shell_injection"),
    "secret_file_read":     _simple("nuguard.redteam.scenarios.coding_agents", "build_secret_file_read"),
    "sandbox_escape":       _simple("nuguard.redteam.scenarios.coding_agents", "build_sandbox_escape"),
    "delayed_ci_exfil":     _simple("nuguard.redteam.scenarios.coding_agents", "build_delayed_ci_exfil"),
    "verifier_sabotage":    _simple("nuguard.redteam.scenarios.coding_agents", "build_verifier_sabotage"),
    # ── RAG and vector store (R-series) ───────────────────────────────────────
    "rag_doc_poisoning":        _build_rag_doc_poisoning,
    "vector_acl_bypass":        _build_vector_acl_bypass,
    "embedding_hijack":         _build_embedding_hijack,
    "chunk_boundary_smuggling": _build_chunk_boundary_smuggling,
    "stale_retrieval":          _build_stale_retrieval,
    "cross_namespace_bleed":    _build_cross_namespace_bleed,
    "citation_laundering":      _build_citation_laundering,
    "nn_enumeration":           _build_nn_enumeration,
    # ── Improper output handling (O-series) ───────────────────────────────────
    "output_xss":               _build_output_xss,
    "output_tool_injection":    _build_output_tool_injection,
    "output_sql_bypass":        _build_output_sql_bypass,
    "output_ssrf":              _build_output_ssrf,
    "output_config_injection":  _build_output_config_injection,
    "output_file_confusion":    _build_output_file_confusion,
    # ── Human-agent trust (H-series) ──────────────────────────────────────────
    "approval_mismatch":        _build_approval_mismatch,
    "consent_laundering":       _build_consent_laundering,
    "authority_bias":           _build_authority_bias,
    "partial_overreach":        _build_partial_overreach,
    "hidden_payload":           _build_hidden_payload,
    # ── Agent identity (N-series) ──────────────────────────────────────────────
    "oauth_scope_escalation":   _build_oauth_scope_escalation,
    "token_replay":             _build_token_replay,
    "ownerless_action":         _build_ownerless_action,
    "cross_agent_cred_bleed":   _build_cross_agent_cred_bleed,
    "delegated_identity":       _build_delegated_identity,
    "credential_persistence":   _build_credential_persistence,
    # ── API schema exploitation (S-series) ────────────────────────────────────
    "schema_identity_override":   _build_schema_identity_override,
    "schema_mass_assignment":     _build_schema_mass_assignment,
    "response_schema_probe":      _build_response_schema_probe,
    "streaming_exfil":            _build_streaming_exfil,
    "param_confusion_injection":  _build_param_confusion_injection,
    "session_fixation":           _build_session_fixation,
    "structured_output_coercion": _build_structured_output_coercion,
    "schema_type_confusion":      _build_schema_type_confusion,
    # ── Supply chain, CI/CD & deployment (V-series) ───────────────────────────
    "env_var_probe":              _build_env_var_probe,
    "ci_secret_probe":            _build_ci_secret_probe,
    "cloud_metadata_ssrf":        _build_cloud_metadata_ssrf,
    "dependency_cve_probe":       _build_dependency_cve_probe,
    "quality_gate_inference":     _build_quality_gate_inference,
    "artifact_integrity_probe":   _build_artifact_integrity_probe,
    "cross_env_credential_reuse": _build_cross_env_credential_reuse,
}
