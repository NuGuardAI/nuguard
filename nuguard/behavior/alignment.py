"""Static SBOM x Policy alignment checks (BA-001 through BA-016).

All checks are deterministic — no LLM needed.  Each check function returns
0 or more Finding objects.
"""
from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from nuguard.behavior.sbom_graph import SbomGraph
from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity

if TYPE_CHECKING:
    from nuguard.behavior.models import IntentProfile
    from nuguard.models.policy import CognitivePolicy
    from nuguard.sbom.models import AiSbomDocument, Edge, Node

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helper: build node and edge maps
# ---------------------------------------------------------------------------


def _build_node_maps(
    sbom: "AiSbomDocument",
) -> tuple[dict[str, "Node"], dict[str, list["Edge"]]]:
    """Return (node_by_id, outgoing_edges_by_source_id) maps."""
    node_by_id: dict[str, "Node"] = {str(n.id): n for n in sbom.nodes}
    outgoing: dict[str, list["Edge"]] = {}
    for edge in sbom.edges:
        outgoing.setdefault(str(edge.source), []).append(edge)
    return node_by_id, outgoing


def _fuzzy_topic_match(text: str, topics: list[str]) -> list[str]:
    """Return topics that fuzzy-match against *text*.

    A topic matches when:
    - it appears as a word-boundary substring (≥ 4 chars), OR
    - at least one word from the topic appears in the text.
    """
    matches: list[str] = []
    lower_text = text.lower()
    for topic in topics:
        topic_lower = topic.lower()
        # Word-boundary substring match for topics ≥ 4 chars
        if len(topic_lower) >= 4:
            pattern = r"\b" + re.escape(topic_lower) + r"\b"
            if re.search(pattern, lower_text):
                matches.append(topic)
                continue
        # Word-overlap fallback: any word from the topic that's ≥ 4 chars
        for word in topic_lower.split():
            if len(word) >= 4 and word in lower_text:
                matches.append(topic)
                break
    return matches


def _node_type(node: "Node") -> str:
    """Return normalised node type string (e.g. 'AGENT', 'TOOL')."""
    ct = getattr(node, "component_type", None)
    if ct is None:
        return ""
    # ComponentType is a str-enum; .value returns the plain string
    return getattr(ct, "value", str(ct)).upper()


def _edge_type(edge: "Edge") -> str:
    """Return normalised edge relationship type string (e.g. 'CALLS', 'PROTECTS')."""
    rt = getattr(edge, "relationship_type", None)
    if rt is None:
        return ""
    return getattr(rt, "value", str(rt)).upper()


def _node_metadata(node: "Node", key: str, default: object = None) -> object:
    """Safely retrieve a metadata field from a node.

    Works with real NodeMetadata Pydantic objects and MagicMock test fixtures.
    """
    meta = getattr(node, "metadata", None)
    if meta is None:
        return default
    # Real NodeMetadata — use attribute access
    if hasattr(meta, key):
        val = getattr(meta, key, default)
        return val if val is not None else default
    # Fallback: dict-like access (e.g. in tests using plain dicts)
    if hasattr(meta, "get"):
        return meta.get(key, default)
    return default


# ---------------------------------------------------------------------------
# BA-001: System-prompt excerpt mentions a restricted topic
# ---------------------------------------------------------------------------


def _ba_001_system_prompt_restricted_topic(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    if not policy.restricted_topics:
        return []
    findings: list[Finding] = []
    for node in sbom.nodes:
        if _node_type(node) != "AGENT":
            continue
        excerpt = str(_node_metadata(node, "system_prompt_excerpt", "") or "")
        if not excerpt:
            continue
        matched = _fuzzy_topic_match(excerpt, policy.restricted_topics)
        for topic in matched:
            name = getattr(node, "name", None) or str(node.id)
            findings.append(
                Finding(
                    finding_id=f"BA-001-{uuid.uuid4().hex[:8]}",
                    title=f"Agent system prompt references restricted topic: '{topic}'",
                    severity=Severity.HIGH,
                    description=(
                        f"Agent '{name}' has a system_prompt_excerpt that mentions the "
                        f"restricted topic '{topic}'. This may cause the agent to engage "
                        f"with topics it should refuse."
                    ),
                    affected_component=name,
                    remediation=f"Remove references to '{topic}' from {name}'s system prompt.",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# BA-002: Risky tool with no guardrail
# ---------------------------------------------------------------------------


def _ba_002_risky_tool_no_guardrail(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    findings: list[Finding] = []
    # Build set of tool IDs that have an incoming PROTECTS edge
    protected_ids: set[str] = set()
    for edge in sbom.edges:
        if _edge_type(edge) == "PROTECTS":
            protected_ids.add(str(edge.source))
            protected_ids.add(str(edge.target))

    for node in sbom.nodes:
        if _node_type(node) != "TOOL":
            continue
        is_risky = _node_metadata(node, "sql_injectable") or _node_metadata(node, "ssrf_possible")
        if not is_risky:
            continue
        name = getattr(node, "name", None) or str(node.id)
        if str(node.id) not in protected_ids and name not in protected_ids:
            risk_type = "SQL injection" if _node_metadata(node, "sql_injectable") else "SSRF"
            findings.append(
                Finding(
                    finding_id=f"BA-002-{uuid.uuid4().hex[:8]}",
                    title=f"Risky tool '{name}' has no guardrail",
                    severity=Severity.HIGH,
                    description=(
                        f"Tool '{name}' is flagged as {risk_type}-vulnerable "
                        f"but has no PROTECTED_BY guardrail edge in the SBOM."
                    ),
                    affected_component=name,
                    remediation=f"Add an input validation guardrail before {name}.",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# BA-003: Restricted-action tool reachable via CALLS edge
# ---------------------------------------------------------------------------


def _ba_003_restricted_action_tool_edge(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    if not policy.restricted_actions:
        return []
    node_by_id, outgoing = _build_node_maps(sbom)

    # Build map of tool name/id -> tool node
    tool_nodes: dict[str, "Node"] = {}
    for node in sbom.nodes:
        if _node_type(node) == "TOOL":
            tool_nodes[str(node.id)] = node
            name = getattr(node, "name", None)
            if name:
                tool_nodes[name] = node

    # Collect (tool_name, action) -> [agent_names] before emitting findings.
    # Grouping by (tool, action) prevents the N-agent cross-product explosion
    # that occurs in fully-connected agent graphs (e.g. OpenAI Agents SDK).
    grouped: dict[tuple[str, str], list[str]] = {}

    for action in policy.restricted_actions:
        # Find tools whose name or description matches the restricted action
        for node in sbom.nodes:
            if _node_type(node) != "TOOL":
                continue
            name = getattr(node, "name", None) or str(node.id)
            desc = str(_node_metadata(node, "description") or "")
            combined = f"{name} {desc}".lower()
            if not _fuzzy_topic_match(combined, [action]):
                continue
            # Collect agents that can reach this tool
            for src_node in sbom.nodes:
                if _node_type(src_node) != "AGENT":
                    continue
                for edge in outgoing.get(str(src_node.id), []):
                    if _edge_type(edge) == "CALLS" and str(edge.target) in (str(node.id), name):
                        agent_name = getattr(src_node, "name", None) or str(src_node.id)
                        key = (name, action)
                        grouped.setdefault(key, [])
                        if agent_name not in grouped[key]:
                            grouped[key].append(agent_name)

    # Emit one finding per (tool, action) group
    findings: list[Finding] = []
    for (tool_name, action), agent_names in grouped.items():
        if len(agent_names) == 1:
            agent_name = agent_names[0]
            description = (
                f"Policy restricts action '{action}', but agent '{agent_name}' "
                f"has a CALLS edge to tool '{tool_name}' which implements this action."
            )
            remediation = (
                f"Remove or guard the CALLS edge from '{agent_name}' to "
                f"'{tool_name}', or restrict the tool's access."
            )
        else:
            agents_str = ", ".join(f"'{a}'" for a in sorted(agent_names))
            description = (
                f"Policy restricts action '{action}', but {len(agent_names)} agents "
                f"({agents_str}) can reach tool '{tool_name}' via CALLS edges. "
                f"In a fully-connected agent graph this means any agent can invoke "
                f"this restricted action."
            )
            remediation = (
                f"Add an authorisation guard on tool '{tool_name}' that validates "
                f"the calling agent's role before executing the restricted action, "
                f"or remove CALLS edges from agents that should not invoke it."
            )
        findings.append(
            Finding(
                finding_id=f"BA-003-{uuid.uuid4().hex[:8]}",
                title=f"Tool '{tool_name}' implements restricted action and is reachable from {len(agent_names)} agent(s)",
                severity=Severity.HIGH,
                description=description,
                affected_component=tool_name,
                remediation=remediation,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# BA-004: PII datastore without guardrail
# ---------------------------------------------------------------------------


def _ba_004_pii_datastore_no_guardrail(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    findings: list[Finding] = []
    protected_ids: set[str] = set()
    for edge in sbom.edges:
        if _edge_type(edge) == "PROTECTS":
            protected_ids.add(str(edge.source))
            protected_ids.add(str(edge.target))

    for node in sbom.nodes:
        if _node_type(node) != "DATASTORE":
            continue
        has_sensitive = (
            _node_metadata(node, "pii_fields")
            or _node_metadata(node, "phi_fields")
            or _node_metadata(node, "pfi_fields")
            or _node_metadata(node, "classified_tables")
        )
        if not has_sensitive:
            continue
        name = getattr(node, "name", None) or str(node.id)
        if str(node.id) not in protected_ids and name not in protected_ids:
            findings.append(
                Finding(
                    finding_id=f"BA-004-{uuid.uuid4().hex[:8]}",
                    title=f"Sensitive datastore '{name}' has no guardrail",
                    severity=Severity.CRITICAL,
                    description=(
                        f"Datastore '{name}' contains PII, PHI, PFI, or classified data "
                        f"but has no PROTECTED_BY guardrail edge in the SBOM."
                    ),
                    affected_component=name,
                    remediation=f"Add a data-access guardrail protecting '{name}'.",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# BA-005: No-auth agent accessing high-privilege tool
# ---------------------------------------------------------------------------


def _ba_005_no_auth_high_privilege(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    findings: list[Finding] = []
    _, outgoing = _build_node_maps(sbom)

    for node in sbom.nodes:
        if _node_type(node) != "AGENT":
            continue
        if not _node_metadata(node, "no_auth_required"):
            continue
        agent_name = getattr(node, "name", None) or str(node.id)
        for edge in outgoing.get(str(node.id), []):
            if _edge_type(edge) != "CALLS":
                continue
            # Find the target node
            target_id_str = str(edge.target)
            for candidate in sbom.nodes:
                if str(candidate.id) != target_id_str:
                    continue
                if _node_metadata(candidate, "high_privilege"):
                    tool_name = getattr(candidate, "name", None) or str(candidate.id)
                    findings.append(
                        Finding(
                            finding_id=f"BA-005-{uuid.uuid4().hex[:8]}",
                            title=f"Unauthenticated agent '{agent_name}' can access high-privilege tool '{tool_name}'",
                            severity=Severity.CRITICAL,
                            description=(
                                f"Agent '{agent_name}' does not require authentication but "
                                f"has access to high-privilege tool '{tool_name}'."
                            ),
                            affected_component=agent_name,
                            remediation=f"Add authentication requirement to '{agent_name}'.",
                        )
                    )
    return findings


# ---------------------------------------------------------------------------
# BA-006: Untrusted MCP server with write-capable tool
# ---------------------------------------------------------------------------


def _ba_006_untrusted_mcp_write(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    findings: list[Finding] = []
    _, outgoing = _build_node_maps(sbom)

    write_keywords = re.compile(r"\b(?:write|update|delete|insert|modify|create|post|put|patch)\b", re.IGNORECASE)

    for node in sbom.nodes:
        # MCP nodes may appear as FRAMEWORK type with framework="mcp" or have mcp_server_url
        node_type = _node_type(node)
        is_mcp = node_type in ("MCP_SERVER", "MCPSERVER") or (
            node_type == "FRAMEWORK"
            and str(_node_metadata(node, "framework") or "").lower() in ("mcp", "mcp-server")
        ) or bool(_node_metadata(node, "mcp_server_url"))
        if not is_mcp:
            continue
        if str(_node_metadata(node, "trust_level") or "").lower() != "untrusted":
            continue
        server_name = getattr(node, "name", None) or str(node.id)
        for edge in outgoing.get(str(node.id), []):
            if _edge_type(edge) != "CALLS":
                continue
            for candidate in sbom.nodes:
                if str(candidate.id) != str(edge.target):
                    continue
                tool_name = getattr(candidate, "name", None) or str(candidate.id)
                desc = str(_node_metadata(candidate, "description") or "")
                # Check edge access_type (direct field on Edge model)
                edge_access = getattr(edge, "access_type", None)
                access_type = str(getattr(edge_access, "value", edge_access) or "").lower()
                is_write = (
                    "write" in access_type
                    or "readwrite" in access_type
                    or write_keywords.search(desc)
                )
                if is_write:
                    findings.append(
                        Finding(
                            finding_id=f"BA-006-{uuid.uuid4().hex[:8]}",
                            title=f"Untrusted MCP server '{server_name}' has write access via '{tool_name}'",
                            severity=Severity.HIGH,
                            description=(
                                f"MCP server '{server_name}' is marked untrusted but has a CALLS "
                                f"edge to write-capable tool '{tool_name}'."
                            ),
                            affected_component=server_name,
                            remediation=(
                                f"Restrict or remove write-capable tool access for untrusted "
                                f"MCP server '{server_name}'."
                            ),
                        )
                    )
    return findings


# ---------------------------------------------------------------------------
# BA-007: Agent blocked_topics doesn't cover all restricted_topics
# ---------------------------------------------------------------------------


def _ba_007_blocked_topics_gap(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    if not policy.restricted_topics:
        return []
    findings: list[Finding] = []
    for node in sbom.nodes:
        if _node_type(node) != "AGENT":
            continue
        blocked_raw = _node_metadata(node, "blocked_topics") or []
        blocked = {str(t).lower() for t in (blocked_raw if isinstance(blocked_raw, (list, tuple)) else [])}
        agent_name = getattr(node, "name", None) or str(node.id)
        uncovered: list[str] = []
        for topic in policy.restricted_topics:
            if not any(topic.lower() in b or b in topic.lower() for b in blocked):
                uncovered.append(topic)
        if uncovered:
            findings.append(
                Finding(
                    finding_id=f"BA-007-{uuid.uuid4().hex[:8]}",
                    title=f"Agent '{agent_name}' blocked_topics misses {len(uncovered)} restricted topic(s)",
                    severity=Severity.MEDIUM,
                    description=(
                        f"Policy restricts topics {uncovered!r} but agent '{agent_name}' "
                        f"does not include them in blocked_topics."
                    ),
                    affected_component=agent_name,
                    remediation=(
                        f"Add {uncovered!r} to '{agent_name}'s blocked_topics configuration."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# BA-008: No HITL gate for hitl_triggers
# ---------------------------------------------------------------------------


def _ba_008_hitl_gate_missing(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    if not policy.hitl_triggers:
        return []
    findings: list[Finding] = []

    # Look for GUARDRAIL nodes or agents with hitl-related metadata
    guardrail_nodes = [n for n in sbom.nodes if _node_type(n) == "GUARDRAIL"]
    hitl_texts: list[str] = []
    for gn in guardrail_nodes:
        desc = str(_node_metadata(gn, "description") or "")
        hitl_texts.append(desc.lower())
        hitl_texts.append(str(_node_metadata(gn, "rules_excerpt") or "").lower())

    # Also check agent metadata for hitl configurations
    for node in sbom.nodes:
        if _node_type(node) == "AGENT":
            # Use extras dict if present (open-ended metadata)
            extras = _node_metadata(node, "extras") or {}
            extras_dict: dict = extras if isinstance(extras, dict) else {}
            hitl_texts.append(str(extras_dict.get("hitl_triggers", "") or "").lower())
            hitl_texts.append(str(extras_dict.get("escalation_triggers", "") or "").lower())

    combined_hitl_text = " ".join(hitl_texts)

    for trigger in policy.hitl_triggers:
        # Check if any guardrail or agent configuration covers this trigger
        trigger_words = [w for w in trigger.lower().split() if len(w) >= 4]
        covered = any(word in combined_hitl_text for word in trigger_words)
        if not covered:
            findings.append(
                Finding(
                    finding_id=f"BA-008-{uuid.uuid4().hex[:8]}",
                    title=f"No HITL gate detected for trigger: '{trigger}'",
                    severity=Severity.HIGH,
                    description=(
                        f"Policy requires human-in-the-loop when '{trigger}' occurs, "
                        f"but no GUARDRAIL node or agent HITL configuration was found "
                        f"in the SBOM to implement this gate."
                    ),
                    affected_component="system",
                    remediation=(
                        f"Add a GUARDRAIL node or configure HITL escalation for '{trigger}'."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# BA-009: AUTH node does not protect sensitive endpoints/agents
# ---------------------------------------------------------------------------


def _ba_009_unprotected_sensitive_endpoints(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """AUTH → PROTECTS gaps: sensitive endpoints or agents lack an AUTH protection edge."""
    g = SbomGraph(sbom)
    findings: list[Finding] = []

    for node in sbom.nodes:
        ntype = _node_type(node)
        if ntype not in ("API_ENDPOINT", "AGENT"):
            continue
        returns_sensitive = bool(_node_metadata(node, "returns_sensitive_data"))
        if not returns_sensitive:
            continue
        # If the node explicitly declares auth_required=True, the enforcement is present
        if bool(_node_metadata(node, "auth_required")):
            continue
        # no_auth_required=True means deliberately open — skip
        if bool(_node_metadata(node, "no_auth_required")):
            continue
        # Check for incoming AUTH → PROTECTS edge
        if g.has_protection(node.id):
            continue
        name = getattr(node, "name", None) or str(node.id)
        findings.append(
            Finding(
                finding_id=f"BA-009-{uuid.uuid4().hex[:8]}",
                title=f"Sensitive {ntype} '{name}' lacks AUTH protection",
                severity=Severity.HIGH,
                description=(
                    f"{ntype} '{name}' returns sensitive data but has no auth_required=True "
                    f"flag and no AUTH → PROTECTS edge in the SBOM."
                ),
                affected_component=name,
                remediation=(
                    f"Set auth_required=True on '{name}' or add an AUTH node with a "
                    "PROTECTS edge to enforce access control."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# BA-010: High-privilege component reachable without AUTH or GUARDRAIL
# ---------------------------------------------------------------------------


def _ba_010_unguarded_privilege(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """PRIVILEGE nodes or high_privilege tools reachable without AUTH or GUARDRAIL."""
    g = SbomGraph(sbom)
    findings: list[Finding] = []

    # Collect high-privilege nodes (PRIVILEGE type or tool/agent with high_privilege flag)
    high_priv_nodes = list(g.nodes_of_type("PRIVILEGE"))
    for node in sbom.nodes:
        ntype = _node_type(node)
        if ntype in ("TOOL", "AGENT") and bool(_node_metadata(node, "high_privilege")):
            high_priv_nodes.append(node)

    for node in high_priv_nodes:
        name = getattr(node, "name", None) or str(node.id)
        if g.has_protection(node.id):
            continue
        findings.append(
            Finding(
                finding_id=f"BA-010-{uuid.uuid4().hex[:8]}",
                title=f"High-privilege component '{name}' has no AUTH/GUARDRAIL protection",
                severity=Severity.CRITICAL,
                description=(
                    f"'{name}' is marked as high-privilege but has no incoming AUTH → PROTECTS "
                    f"or GUARDRAIL → PROTECTS edge. Privilege escalation is possible."
                ),
                affected_component=name,
                remediation=(
                    f"Add an AUTH or GUARDRAIL node with a PROTECTS edge to '{name}'."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# BA-011: DATASTORE with write access lacks HITL/auth/guardrail
# ---------------------------------------------------------------------------


def _ba_011_write_datastore_no_control(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """DATASTORE reached via ACCESSES(write|readwrite) without HITL, auth, or guardrail."""
    g = SbomGraph(sbom)
    findings: list[Finding] = []

    for edge in sbom.edges:
        rel = _edge_type(edge)
        if rel != "ACCESSES":
            continue
        at = getattr(edge, "access_type", None)
        at_str = str(at.value if hasattr(at, "value") else at).lower() if at else ""
        if at_str not in ("write", "readwrite"):
            continue

        target = g.node_by_id(edge.target)
        if target is None or _node_type(target) != "DATASTORE":
            continue
        ds_name = getattr(target, "name", None) or str(target.id)

        if g.has_protection(target.id):
            continue

        # Check source node for auth metadata
        source = g.node_by_id(edge.source)
        src_name = getattr(source, "name", None) or str(edge.source) if source else str(edge.source)
        src_has_auth = bool(_node_metadata(source, "auth_required")) if source else False
        if src_has_auth:
            continue

        findings.append(
            Finding(
                finding_id=f"BA-011-{uuid.uuid4().hex[:8]}",
                title=f"Write access to '{ds_name}' lacks HITL/auth/guardrail control",
                severity=Severity.HIGH,
                description=(
                    f"'{src_name}' has ACCESSES(write) to datastore '{ds_name}' with no "
                    f"GUARDRAIL or AUTH protection on the datastore path."
                ),
                affected_component=ds_name,
                remediation=(
                    f"Add HITL, an AUTH node, or a GUARDRAIL node with a PROTECTS edge "
                    f"to '{ds_name}' to control write access."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# BA-012: External MODEL used by agent that accesses sensitive data
# ---------------------------------------------------------------------------


def _ba_012_external_model_sensitive_data(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """AGENT → USES → MODEL (external) AND agent transitively reaches sensitive DATASTORE."""
    g = SbomGraph(sbom)
    findings: list[Finding] = []

    sensitive_ds_ids = {
        str(n.id)
        for n in sbom.nodes
        if _node_type(n) == "DATASTORE"
        and (
            _node_metadata(n, "pii_fields")
            or _node_metadata(n, "phi_fields")
            or _node_metadata(n, "pfi_fields")
        )
    }
    if not sensitive_ds_ids:
        return []

    for agent in g.nodes_of_type("AGENT"):
        # Check for USES → MODEL edges with external source_url
        external_models = []
        for model_node in g.targets(agent.id, "USES"):
            if _node_type(model_node) != "MODEL":
                continue
            src_url = str(_node_metadata(model_node, "source_url") or "")
            if src_url.startswith(("http://", "https://")) or "huggingface" in src_url:
                external_models.append(model_node)

        if not external_models:
            continue

        # Check if agent transitively reaches sensitive datastore
        paths = g.accesses_paths(agent.id)
        agent_ds_ids = {str(ds_node.id) for _, ds_node, _ in paths}
        if not (agent_ds_ids & sensitive_ds_ids):
            continue

        agent_name = getattr(agent, "name", None) or str(agent.id)
        model_names = [getattr(m, "name", None) or str(m.id) for m in external_models]
        findings.append(
            Finding(
                finding_id=f"BA-012-{uuid.uuid4().hex[:8]}",
                title=f"Agent '{agent_name}' uses external model with access to sensitive data",
                severity=Severity.MEDIUM,
                description=(
                    f"'{agent_name}' uses external model(s) [{', '.join(model_names)}] "
                    f"and can reach sensitive datastores. Supply-chain risk if the model "
                    f"is replaced or compromised."
                ),
                affected_component=agent_name,
                remediation=(
                    "Pin the model version with an integrity_hash or checksum. "
                    "Consider isolating sensitive data paths from externally-sourced models."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# BA-013: AGENT → USES → PROMPT contains restricted topic
# ---------------------------------------------------------------------------


def _ba_013_prompt_restricted_topic(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """AGENT → USES → PROMPT where prompt text contains a policy restricted topic."""
    if not getattr(policy, "restricted_topics", None):
        return []

    g = SbomGraph(sbom)
    findings: list[Finding] = []
    restricted: list[str] = list(policy.restricted_topics)

    for agent in g.nodes_of_type("AGENT"):
        for prompt_node in g.targets(agent.id, "USES"):
            if _node_type(prompt_node) != "PROMPT":
                continue
            excerpt = (
                str(_node_metadata(prompt_node, "rules_excerpt") or "")
                + " "
                + str(_node_metadata(prompt_node, "system_prompt_excerpt") or "")
            ).lower()
            if not excerpt.strip():
                continue
            matches = _fuzzy_topic_match(excerpt, restricted)
            if not matches:
                continue
            agent_name = getattr(agent, "name", None) or str(agent.id)
            prompt_name = getattr(prompt_node, "name", None) or str(prompt_node.id)
            findings.append(
                Finding(
                    finding_id=f"BA-013-{uuid.uuid4().hex[:8]}",
                    title=(
                        f"Prompt '{prompt_name}' for agent '{agent_name}' "
                        f"references restricted topic(s)"
                    ),
                    severity=Severity.HIGH,
                    description=(
                        f"Agent '{agent_name}' uses prompt '{prompt_name}' whose "
                        f"text matches policy restricted topic(s): {', '.join(matches)}."
                    ),
                    affected_component=agent_name,
                    remediation=(
                        f"Review prompt '{prompt_name}' and remove or reframe references "
                        f"to restricted topics: {', '.join(matches)}."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# BA-014: DELEGATES_TO handoff to higher-privilege agent without boundary
# ---------------------------------------------------------------------------


def _ba_014_unsafe_delegation(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """AGENT → DELEGATES_TO → AGENT where target is higher-privilege with no guardrail."""
    g = SbomGraph(sbom)
    findings: list[Finding] = []

    for agent in g.nodes_of_type("AGENT"):
        src_high_priv = bool(_node_metadata(agent, "high_privilege"))
        src_no_auth = bool(_node_metadata(agent, "no_auth_required"))

        for downstream in g.targets(agent.id, "DELEGATES_TO"):
            tgt_high_priv = bool(_node_metadata(downstream, "high_privilege"))
            tgt_no_auth = bool(_node_metadata(downstream, "no_auth_required"))

            # Privilege escalation: target is high_privilege but source is not
            escalates = tgt_high_priv and not src_high_priv
            # Auth weakening: source requires auth but target doesn't
            weakens_auth = not src_no_auth and tgt_no_auth

            if not (escalates or weakens_auth):
                continue

            # Check if downstream is protected
            if g.has_protection(downstream.id):
                continue

            src_name = getattr(agent, "name", None) or str(agent.id)
            tgt_name = getattr(downstream, "name", None) or str(downstream.id)
            reason = []
            if escalates:
                reason.append("target is high-privilege")
            if weakens_auth:
                reason.append("target drops auth requirement")
            findings.append(
                Finding(
                    finding_id=f"BA-014-{uuid.uuid4().hex[:8]}",
                    title=(
                        f"Unsafe delegation from '{src_name}' to '{tgt_name}' "
                        f"({', '.join(reason)})"
                    ),
                    severity=Severity.HIGH,
                    description=(
                        f"'{src_name}' delegates to '{tgt_name}' which {' and '.join(reason)}, "
                        f"but no GUARDRAIL or AUTH PROTECTS boundary guards the handoff."
                    ),
                    affected_component=src_name,
                    remediation=(
                        f"Add a GUARDRAIL or AUTH node with a PROTECTS edge to '{tgt_name}' "
                        f"to enforce a privilege boundary on the delegation path."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# BA-015: DEPLOYS path has security posture issues
# ---------------------------------------------------------------------------


def _ba_015_deployment_security_posture(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """DEPLOYS edge where the deployment node has known security posture issues."""
    g = SbomGraph(sbom)
    findings: list[Finding] = []

    for edge in sbom.edges:
        if _edge_type(edge) != "DEPLOYS":
            continue
        deploy_node = g.node_by_id(edge.target)
        if deploy_node is None or _node_type(deploy_node) not in ("DEPLOYMENT", "CONTAINER_IMAGE"):
            continue

        name = getattr(deploy_node, "name", None) or str(deploy_node.id)
        issues: list[str] = []

        if bool(_node_metadata(deploy_node, "runs_as_root")):
            issues.append("container runs as root")
        if _node_metadata(deploy_node, "has_health_check") is False:
            issues.append("no health check configured")
        if _node_metadata(deploy_node, "has_resource_limits") is False:
            issues.append("no resource limits set")
        if _node_metadata(deploy_node, "has_network_policy") is False:
            issues.append("no network policy")

        if not issues:
            continue

        source = g.node_by_id(edge.source)
        src_name = getattr(source, "name", None) or str(edge.source) if source else str(edge.source)
        findings.append(
            Finding(
                finding_id=f"BA-015-{uuid.uuid4().hex[:8]}",
                title=f"Deployment '{name}' has security posture issues",
                severity=Severity.MEDIUM,
                description=(
                    f"Deployment '{name}' (deployed by '{src_name}') has the following "
                    f"issues: {', '.join(issues)}."
                ),
                affected_component=name,
                remediation=(
                    f"Address deployment posture for '{name}': "
                    + "; ".join(issues) + "."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# BA-016: API_ENDPOINT returns sensitive data without auth or guardrail
# ---------------------------------------------------------------------------


def _ba_016_sensitive_endpoint_no_protection(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """API_ENDPOINT with returns_sensitive_data=True but no auth or guardrail protection."""
    g = SbomGraph(sbom)
    findings: list[Finding] = []

    for node in g.nodes_of_type("API_ENDPOINT"):
        returns_sensitive = bool(_node_metadata(node, "returns_sensitive_data"))
        if not returns_sensitive:
            continue
        auth_required = bool(_node_metadata(node, "auth_required"))
        if auth_required:
            continue
        if g.has_protection(node.id):
            continue
        name = getattr(node, "name", None) or str(node.id)
        findings.append(
            Finding(
                finding_id=f"BA-016-{uuid.uuid4().hex[:8]}",
                title=f"Sensitive endpoint '{name}' lacks auth and guardrail protection",
                severity=Severity.HIGH,
                description=(
                    f"API endpoint '{name}' returns sensitive data but has no "
                    f"auth_required flag, no AUTH → PROTECTS, and no GUARDRAIL → PROTECTS edge."
                ),
                affected_component=name,
                remediation=(
                    f"Set auth_required=True on '{name}' and add an AUTH or GUARDRAIL "
                    f"node with a PROTECTS edge."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def check_alignment(
    sbom: "AiSbomDocument",
    intent: "IntentProfile",
    policy: "CognitivePolicy",
) -> list[Finding]:
    """Run all behavior alignment checks and return findings.

    Each check function returns 0 or more Finding objects.
    All checks are deterministic — no LLM needed.

    Args:
        sbom: The AI-SBOM document to analyze.
        intent: The extracted IntentProfile.
        policy: The parsed CognitivePolicy.

    Returns:
        List of Finding objects from all checks.
    """
    checkers = [
        _ba_001_system_prompt_restricted_topic,
        _ba_002_risky_tool_no_guardrail,
        _ba_003_restricted_action_tool_edge,
        _ba_004_pii_datastore_no_guardrail,
        _ba_005_no_auth_high_privilege,
        _ba_006_untrusted_mcp_write,
        _ba_007_blocked_topics_gap,
        _ba_008_hitl_gate_missing,
        # Expanded graph checks (P1)
        _ba_009_unprotected_sensitive_endpoints,
        _ba_010_unguarded_privilege,
        _ba_011_write_datastore_no_control,
        _ba_012_external_model_sensitive_data,
        _ba_013_prompt_restricted_topic,
        _ba_014_unsafe_delegation,
        _ba_015_deployment_security_posture,
        _ba_016_sensitive_endpoint_no_protection,
    ]
    all_findings: list[Finding] = []
    for checker in checkers:
        try:
            results = checker(sbom, intent, policy)
            all_findings.extend(results)
        except Exception as exc:
            _log.warning("check_alignment: checker %s failed: %s", checker.__name__, exc)
    _log.debug("check_alignment: %d findings from %d checks", len(all_findings), len(checkers))
    return all_findings
