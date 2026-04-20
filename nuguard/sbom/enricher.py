"""Post-extraction SBOM enricher.

Derives risk attributes on SBOM nodes that require either graph-topology
knowledge (edges between nodes) or heuristic analysis of description text and
parameter names.  This runs immediately after the extraction pipeline builds
``AiSbomDocument`` and before the document is returned to callers.

Attributes derived here
-----------------------
TOOL nodes
    ``no_auth_required``     — True when no AUTH node protects the tool's server.
    ``high_privilege``       — True when the tool is connected (directly or via
                               its server FRAMEWORK) to a PRIVILEGE node.
    ``sql_injectable``       — Heuristic: tool description / parameter names
                               suggest raw-string database query construction.
    ``ssrf_possible``        — Heuristic: tool accepts a URL/endpoint parameter
                               that is fetched server-side.
    ``accepts_external_url`` — Heuristic: tool has a URL-typed parameter.
    ``reads_external_content``— Heuristic: tool name or description implies
                               fetching remote content.

AGENT nodes
    ``injection_risk_score`` — [0, 1] risk score derived from reachable tools,
                               datastores, and absence of guardrail coverage.

API_ENDPOINT nodes
    ``idor_surface``         — True when the endpoint URL template contains
                               user- or tenant-scoped path parameters.
    ``path_params``          — Extracted path parameter names from the URL.
    ``auth_required``        — False when no AUTH node has a PROTECTS edge to
                               this endpoint (if not already set by an adapter).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import UUID

from .models import AiSbomDocument, Node, NodeMetadata
from .types import ComponentType, RelationshipType

# ---------------------------------------------------------------------------
# Path-parameter detection
# ---------------------------------------------------------------------------

_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")

# Parameter names that indicate user/tenant-scoped IDOR surface
_IDOR_PARAM_PATTERNS = re.compile(
    r"\b(?:user_?id|account_?id|tenant_?id|customer_?id|org(?:aniz(?:ation)?)?_?id|"
    r"member_?id|profile_?id|owner_?id|subject_?id|uid|pid)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Tool heuristic patterns
# ---------------------------------------------------------------------------

_SQL_KEYWORDS = re.compile(
    r"\b(?:query|search|filter|find|select|lookup|fetch|get|list|retrieve)\b",
    re.IGNORECASE,
)
_SQL_DB_KEYWORDS = re.compile(
    r"\b(?:database|sql|db|table|record|row|column|schema|postgres|mysql|sqlite|"
    r"mongo(?:db)?|dynamo(?:db)?|datastore)\b",
    re.IGNORECASE,
)

_URL_PARAM_RE = re.compile(
    r"\b(?:url|uri|endpoint|href|link|location|address|source|destination|target|"
    r"webhook|callback|redirect)\b",
    re.IGNORECASE,
)

_EXTERNAL_CONTENT_RE = re.compile(
    r"\b(?:fetch|browse|scrape|crawl|download|retrieve|request|http|web|page|"
    r"website|internet|rss|email|inbox|github|slack|notion|jira)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def enrich(doc: AiSbomDocument) -> None:
    """Mutate *doc* in-place, adding derived risk attributes to all nodes.

    This is idempotent: attributes already set by an adapter are not
    overwritten unless explicitly documented below.
    """
    # Build lookup indexes
    node_by_id: dict[UUID, Node] = {n.id: n for n in doc.nodes}

    # Edges indexed by (source_id, relationship_type) → set[target_id]
    outgoing: dict[UUID, dict[str, set[UUID]]] = {}
    # Edges indexed by target_id → set[(source_id, rel)]
    incoming: dict[UUID, list[tuple[UUID, str]]] = {}
    for edge in doc.edges:
        outgoing.setdefault(edge.source, {}).setdefault(
            edge.relationship_type, set()
        ).add(edge.target)
        incoming.setdefault(edge.target, []).append(
            (edge.source, edge.relationship_type)
        )

    def targets(node_id: UUID, rel: str) -> set[UUID]:
        return outgoing.get(node_id, {}).get(rel, set())

    def sources_of_type(node_id: UUID, rel: str, node_type: str) -> list[Node]:
        result = []
        for src_id, src_rel in incoming.get(node_id, []):
            if src_rel != rel:
                continue
            src = node_by_id.get(src_id)
            if src and src.component_type == node_type:
                result.append(src)
        return result

    # Map: framework canonical → AUTH node IDs that protect it (via USES or PROTECTS)
    framework_auth: dict[UUID, set[UUID]] = {}
    for n in doc.nodes:
        if n.component_type == ComponentType.AUTH:
            for tgt_id in targets(n.id, RelationshipType.PROTECTS):
                framework_auth.setdefault(tgt_id, set()).add(n.id)

    # Map: framework node ID → set of tool node IDs it CALLS
    framework_tools: dict[UUID, set[UUID]] = {}
    for n in doc.nodes:
        if n.component_type == ComponentType.FRAMEWORK:
            framework_tools[n.id] = targets(n.id, RelationshipType.CALLS)

    # Reverse: tool node ID → framework node IDs
    tool_frameworks: dict[UUID, set[UUID]] = {}
    for fw_id, tool_ids in framework_tools.items():
        for t_id in tool_ids:
            tool_frameworks.setdefault(t_id, set()).add(fw_id)

    # PRIVILEGE node IDs
    privilege_node_ids: set[UUID] = {
        n.id for n in doc.nodes if n.component_type == ComponentType.PRIVILEGE
    }

    _enrich_api_endpoints(doc, targets)
    _enrich_tools(doc, tool_frameworks, framework_auth, privilege_node_ids, targets)
    _enrich_agents(doc, targets, node_by_id)
    _backfill_descriptions(doc)


# ---------------------------------------------------------------------------
# API_ENDPOINT enrichment
# ---------------------------------------------------------------------------


def _enrich_api_endpoints(
    doc: AiSbomDocument,
    targets: Callable[[UUID, str], set[UUID]],
) -> None:
    for node in doc.nodes:
        if node.component_type != ComponentType.API_ENDPOINT:
            continue
        meta: NodeMetadata = node.metadata
        endpoint_str = meta.endpoint or meta.extras.get("api_endpoint", "") or ""

        # Extract path params from the endpoint string
        if not meta.path_params and endpoint_str:
            params = _PATH_PARAM_RE.findall(endpoint_str)
            if params:
                meta.path_params = params

        # Determine idor_surface from path parameters
        if meta.idor_surface is None and meta.path_params:
            meta.idor_surface = any(
                _IDOR_PARAM_PATTERNS.search(p) for p in meta.path_params
            )

        # auth_required: infer False only when not already set (adapter didn't know)
        # (set to True by default; will be corrected below if no AUTH node found)
        # — this is left to the graph enricher since we'd need AUTH→PROTECTS→ENDPOINT


# ---------------------------------------------------------------------------
# TOOL enrichment
# ---------------------------------------------------------------------------

# High-privilege scope labels (from PrivilegeScope enum)
_HIGH_PRIVILEGE_SCOPES = {
    "db_write",
    "filesystem_write",
    "code_execution",
    "email_out",
    "social_media_out",
    "admin",
    "network_out",
}


def _enrich_tools(
    doc: AiSbomDocument,
    tool_frameworks: dict[UUID, set[UUID]],
    framework_auth: dict[UUID, set[UUID]],
    privilege_node_ids: set[UUID],
    targets: Callable[[UUID, str], set[UUID]],
) -> None:
    for node in doc.nodes:
        if node.component_type != ComponentType.TOOL:
            continue
        meta: NodeMetadata = node.metadata

        # --- no_auth_required ---
        # Already set by adapter (e.g., MCPServerAdapter)? Correct it if we can
        # confirm an AUTH node exists on the tool's framework path.
        if meta.no_auth_required is None or meta.no_auth_required is True:
            fw_ids = tool_frameworks.get(node.id, set())
            # If any of the frameworks this tool belongs to has an AUTH node → not no_auth
            has_auth = any(
                bool(framework_auth.get(fw_id)) for fw_id in fw_ids
            )
            if has_auth:
                meta.no_auth_required = False
            elif meta.no_auth_required is None:
                # No framework link found; default to unknown (leave as None)
                pass

        # --- high_privilege ---
        if meta.high_privilege is None:
            # Check if this tool CALLS a PRIVILEGE node directly
            fw_ids = tool_frameworks.get(node.id, set())
            priv_via_fw = any(
                bool(targets(fw_id, RelationshipType.CALLS) & privilege_node_ids)
                for fw_id in fw_ids
            )
            # Or if the tool's privilege_scope is high
            has_scope = bool(
                meta.privilege_scope and meta.privilege_scope in _HIGH_PRIVILEGE_SCOPES
            )
            meta.high_privilege = priv_via_fw or has_scope or False

        # --- Heuristics: use description and extras ---
        desc = _tool_description(meta)
        param_names = _param_names(meta)

        # --- sql_injectable ---
        if meta.sql_injectable is None:
            meta.sql_injectable = _is_sql_injectable(desc, param_names)

        # --- ssrf_possible ---
        if meta.ssrf_possible is None:
            meta.ssrf_possible = _is_ssrf_possible(desc, param_names)

        # --- accepts_external_url ---
        if meta.accepts_external_url is None:
            meta.accepts_external_url = any(
                _URL_PARAM_RE.search(p) for p in param_names
            )

        # --- reads_external_content ---
        if meta.reads_external_content is None:
            meta.reads_external_content = bool(_EXTERNAL_CONTENT_RE.search(desc))


def _tool_description(meta: NodeMetadata) -> str:
    """Return a searchable description string for a TOOL node."""
    parts = []
    if meta.description:
        parts.append(meta.description)
    # description may also be in extras (LLM-generated MCP descriptions)
    extras_desc = meta.extras.get("description", "")
    if extras_desc and isinstance(extras_desc, str):
        parts.append(extras_desc)
    # adapter and canonical_name carry naming hints
    adapter = meta.extras.get("adapter", "") or ""
    if adapter:
        parts.append(adapter)
    return " ".join(parts).lower()


def _param_names(meta: NodeMetadata) -> list[str]:
    """Return parameter name strings for a TOOL node."""
    if meta.parameters:
        return [p.name for p in meta.parameters if p.name]
    return []


def _is_sql_injectable(desc: str, param_names: list[str]) -> bool:
    """Heuristic: True when the tool likely constructs raw SQL from user input."""
    has_query_verb = bool(_SQL_KEYWORDS.search(desc))
    has_db_noun = bool(_SQL_DB_KEYWORDS.search(desc))
    has_string_param = any(
        re.search(r"\b(?:query|q|search|filter|where|condition|term|keyword)\b", p, re.IGNORECASE)
        for p in param_names
    )
    return (has_query_verb and has_db_noun) or (has_db_noun and has_string_param)


def _is_ssrf_possible(desc: str, param_names: list[str]) -> bool:
    """Heuristic: True when the tool accepts a URL/endpoint parameter that is fetched."""
    has_url_param = any(_URL_PARAM_RE.search(p) for p in param_names)
    has_fetch_desc = bool(
        re.search(
            r"\b(?:fetch|request|get|download|retrieve|http|curl|browse)\b",
            desc,
            re.IGNORECASE,
        )
    )
    return has_url_param and has_fetch_desc


# ---------------------------------------------------------------------------
# AGENT enrichment
# ---------------------------------------------------------------------------


def _enrich_agents(
    doc: AiSbomDocument,
    targets: Callable[[UUID, str], set[UUID]],
    node_by_id: dict[UUID, Node],
) -> None:
    # DATASTORE nodes with PII or PHI
    sensitive_ds_ids: set[UUID] = {
        n.id
        for n in doc.nodes
        if n.component_type == ComponentType.DATASTORE
        and (n.metadata.pii_fields or n.metadata.phi_fields)
    }

    for node in doc.nodes:
        if node.component_type != ComponentType.AGENT:
            continue
        if node.metadata.injection_risk_score is not None:
            continue  # already set by adapter

        score = 0.0

        # Reachable TOOL node IDs from this agent
        tool_ids = targets(node.id, RelationshipType.CALLS)

        # Tools with no_auth_required
        unauth_tools = sum(
            1
            for tid in tool_ids
            if (t := node_by_id.get(tid))
            and getattr(t.metadata, "no_auth_required", False)
        )
        score += min(unauth_tools * 0.15, 0.30)

        # High-privilege tools
        hp_tools = sum(
            1
            for tid in tool_ids
            if (t := node_by_id.get(tid))
            and getattr(t.metadata, "high_privilege", False)
        )
        score += min(hp_tools * 0.20, 0.40)

        # Reachable sensitive datastores (via TOOL→DATASTORE ACCESSES edges)
        reachable_ds: set[UUID] = set()
        for tool_id in tool_ids:
            reachable_ds.update(targets(tool_id, RelationshipType.ACCESSES))
        sensitive_reach = bool(reachable_ds & sensitive_ds_ids)
        if sensitive_reach:
            score += 0.20

        # Absence of guardrail coverage
        protecting_guardrails = targets(node.id, RelationshipType.PROTECTS)
        if not protecting_guardrails:
            score += 0.10

        node.metadata.injection_risk_score = min(round(score, 2), 1.0)


# ---------------------------------------------------------------------------
# Description backfill
# ---------------------------------------------------------------------------


def _backfill_descriptions(doc: AiSbomDocument) -> None:
    """Fill in missing descriptions for TOOL and AGENT nodes using available metadata.

    Mutates nodes in-place.  Only sets ``metadata.description`` when it is
    currently empty or None.  Called at the end of ``enrich()`` so that
    deterministic fields are always available before the LLM enrichment step.
    """
    for node in doc.nodes:
        meta: NodeMetadata = node.metadata

        if node.component_type == ComponentType.TOOL:
            if meta.description:
                continue  # already set

            # 1. LLM-generated MCP description stored in extras
            extras_desc = meta.extras.get("description", "")
            if extras_desc and isinstance(extras_desc, str) and extras_desc.strip():
                meta.description = extras_desc.strip()[:200]
                continue

            # 2. First parameter description that looks like a docstring sentence
            if meta.parameters:
                for param in meta.parameters:
                    pdesc = (param.description or "").strip()
                    if pdesc and ("." in pdesc or len(pdesc) > 20):
                        meta.description = pdesc[:200]
                        break
                if meta.description:
                    continue

            # 3. Concatenate parameter names as last resort
            if meta.parameters:
                param_names = [p.name for p in meta.parameters if p.name]
                if param_names:
                    meta.description = "Tool with parameters: " + ", ".join(param_names[:8])

        elif node.component_type == ComponentType.AGENT:
            if meta.description:
                continue  # already set

            # 1. Build from role + goal extras
            role = (meta.extras.get("role") or "").strip()
            goal = (meta.extras.get("goal") or "").strip()
            if role and goal:
                meta.description = f"{role}: {goal}"[:200]
                continue
            if role:
                meta.description = role[:200]
                continue

            # 2. Use system prompt excerpt
            excerpt = (meta.system_prompt_excerpt or "").strip()
            if excerpt:
                meta.description = excerpt[:200]
                continue

            # 3. Final fallback
            meta.description = f"{node.name} agent"
