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

from .models import AiSbomDocument, InstrumentationDetail, Node, NodeMetadata, TestingDetail
from .types import ComponentType, RelationshipType

# ---------------------------------------------------------------------------
# Path-parameter detection
# ---------------------------------------------------------------------------

# FastAPI/ASP.NET Core style: /user/{id}. Checked first since brace-style
# paths never also use colon params in the same endpoint.
_BRACE_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
# NestJS/Express style: /user/:id. Falls back to this when no {param} was
# found — see tests/apps/studyield-app/2-step-chat.md.
_COLON_PATH_PARAM_RE = re.compile(r":(\w+)")


def _extract_path_params(endpoint: str) -> list[str]:
    params = _BRACE_PATH_PARAM_RE.findall(endpoint)
    if params:
        return params
    return _COLON_PATH_PARAM_RE.findall(endpoint)


def _path_param_candidate_paths(endpoint: str, params: list[str]) -> dict[str, str]:
    """Return ``{param_name: candidate_collection_path}`` for each param in *params*.

    The candidate is everything in *endpoint* before that param's token,
    e.g. ``/orgs/:orgId/projects/:projectId/chat`` -> ``orgId``: ``/orgs``,
    ``projectId``: ``/orgs/:orgId/projects``. Matching is by path *template*
    text (earlier params left as literal tokens), since this is a static
    SBOM-time match, not a runtime one. Params with an empty prefix (no
    plausible collection path) are omitted.
    """
    token_matches = list(_BRACE_PATH_PARAM_RE.finditer(endpoint))
    if not token_matches:
        token_matches = list(_COLON_PATH_PARAM_RE.finditer(endpoint))

    candidates: dict[str, str] = {}
    for m in token_matches:
        name = m.group(1)
        if name not in params:
            continue
        prefix = endpoint[: m.start()].rstrip("/")
        if prefix:
            candidates[name] = prefix
    return candidates

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

    # Map: POST API_ENDPOINT path -> node, for resolving path_param_sources.
    post_endpoints_by_path: dict[str, Node] = {}
    for n in doc.nodes:
        if n.component_type != ComponentType.API_ENDPOINT:
            continue
        m = n.metadata
        if not m or (m.method or "").upper() != "POST":
            continue
        ep = m.endpoint or m.extras.get("api_endpoint", "") or ""
        if ep:
            post_endpoints_by_path.setdefault(ep, n)

    _enrich_api_endpoints(doc, targets, sources_of_type, post_endpoints_by_path)
    _enrich_tools(doc, tool_frameworks, framework_auth, privilege_node_ids, targets)
    _enrich_agents(doc, targets, sources_of_type, node_by_id)
    _backfill_descriptions(doc)
    _enrich_instrumentation(doc)
    _enrich_testing(doc)


# ---------------------------------------------------------------------------
# API_ENDPOINT enrichment
# ---------------------------------------------------------------------------


def _enrich_api_endpoints(
    doc: AiSbomDocument,
    targets: Callable[[UUID, str], set[UUID]],
    sources_of_type: Callable[[UUID, str, str], list[Node]],
    post_endpoints_by_path: dict[str, Node],
) -> None:
    for node in doc.nodes:
        if node.component_type != ComponentType.API_ENDPOINT:
            continue
        meta: NodeMetadata = node.metadata
        endpoint_str = meta.endpoint or meta.extras.get("api_endpoint", "") or ""

        # Extract path params from the endpoint string
        if not meta.path_params and endpoint_str:
            params = _extract_path_params(endpoint_str)
            if params:
                meta.path_params = params

        # Identify which POST endpoint creates the resource each path param
        # identifies (e.g. 'id' on '/chat/conversations/:id/messages' ->
        # '/chat/conversations'), for redteam/behavior's bootstrap step.
        if meta.path_param_sources is None and meta.path_params and endpoint_str:
            candidates = _path_param_candidate_paths(endpoint_str, meta.path_params)
            sources = {
                param: candidate_path
                for param, candidate_path in candidates.items()
                if candidate_path in post_endpoints_by_path
            }
            if sources:
                meta.path_param_sources = sources

        # Determine idor_surface from path parameters
        if meta.idor_surface is None and meta.path_params:
            meta.idor_surface = any(
                _IDOR_PARAM_PATTERNS.search(p) for p in meta.path_params
            )

        # auth_required: derived from AUTH→PROTECTS→ENDPOINT edges, unless an
        # adapter already set it explicitly.
        if meta.auth_required is None:
            protecting_auth = sources_of_type(
                node.id, RelationshipType.PROTECTS, ComponentType.AUTH
            )
            meta.auth_required = bool(protecting_auth)


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
    sources_of_type: Callable[[UUID, str, str], list[Node]],
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
        protecting_guardrails = sources_of_type(
            node.id, RelationshipType.PROTECTS, ComponentType.GUARDRAIL
        )
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


# ---------------------------------------------------------------------------
# Instrumentation enrichment (per-node, uses dependency_names)
# ---------------------------------------------------------------------------

_INSTRUMENTATION_TOOL_MAP: dict[str, str] = {
    "opentelemetry": "opentelemetry",
    "opentelemetry_api": "opentelemetry",
    "opentelemetry_sdk": "opentelemetry",
    "opentelemetry_instrumentation": "opentelemetry",
    "ddtrace": "datadog",
    "datadog": "datadog",
    "prometheus_client": "prometheus",
    "prometheus": "prometheus",
    "structlog": "structlog",
    "sentry_sdk": "sentry",
    "sentry": "sentry",
    "newrelic": "new_relic",
    "elastic_apm": "elastic_apm",
    "jaeger": "jaeger",
    "zipkin": "zipkin",
}


def _enrich_instrumentation(doc: AiSbomDocument) -> None:
    """Derive InstrumentationDetail for each node from its dependency_names."""
    for node in doc.nodes:
        if node.metadata.instrumentation is not None:
            continue  # already set by an adapter
        dep_names = node.metadata.dependency_names or []
        tools: list[str] = []
        seen: set[str] = set()
        for dep in dep_names:
            tool = _INSTRUMENTATION_TOOL_MAP.get(dep.replace("-", "_").lower())
            if tool and tool not in seen:
                tools.append(tool)
                seen.add(tool)
        if not tools:
            continue
        node.metadata.instrumentation = InstrumentationDetail(
            tools=tools,
            tracing_enabled=any(t in seen for t in ("opentelemetry", "datadog", "jaeger", "zipkin")),
            metrics_enabled=any(t in seen for t in ("prometheus", "datadog", "elastic_apm")),
        )


# ---------------------------------------------------------------------------
# Testing enrichment (per-node, heuristic based on evidence file paths)
# ---------------------------------------------------------------------------

_TEST_FRAMEWORK_DEPS: dict[str, str] = {
    "pytest": "pytest",
    "unittest": "unittest",
    "hypothesis": "hypothesis",
    "jest": "jest",
    "vitest": "vitest",
    "mocha": "mocha",
    "jasmine": "jasmine",
    "coverage": "coverage",
    "codecov": "codecov",
    "pytest_cov": "coverage",
}

_TEST_FILE_PATTERNS = re.compile(
    r"(?:^|/)tests?/|_test\.py$|test_[^/]+\.py$|__tests__/|\.spec\.[jt]sx?$|\.test\.[jt]sx?$",
    re.IGNORECASE,
)


def _enrich_testing(doc: AiSbomDocument) -> None:
    """Derive TestingDetail for each node based on evidence file paths and dependencies."""
    # Build a set of all evidence paths across all nodes (for sibling test detection)
    all_ev_paths: set[str] = set()
    for node in doc.nodes:
        for ev in node.evidence:
            if ev.location:
                all_ev_paths.add(ev.location.path)

    for node in doc.nodes:
        if node.metadata.testing is not None:
            continue  # already set by an adapter
        dep_names = node.metadata.dependency_names or []
        frameworks: list[str] = []
        seen_fw: set[str] = set()
        for dep in dep_names:
            fw = _TEST_FRAMEWORK_DEPS.get(dep.replace("-", "_").lower())
            if fw and fw not in seen_fw:
                frameworks.append(fw)
                seen_fw.add(fw)

        has_unit = False
        has_integration = False
        for ev in node.evidence:
            if not ev.location:
                continue
            ev_path = ev.location.path
            # Check if a sibling test file exists among all evidence paths
            stem = ev_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if any(
                p.endswith(f"test_{stem}.py") or p.endswith(f"{stem}_test.py")
                or p.endswith(f"{stem}.test.ts") or p.endswith(f"{stem}.spec.ts")
                for p in all_ev_paths
            ):
                has_unit = True
            if _TEST_FILE_PATTERNS.search(ev_path):
                has_unit = True

        if not (has_unit or has_integration or frameworks):
            continue
        node.metadata.testing = TestingDetail(
            has_unit_tests=has_unit or None,
            has_integration_tests=has_integration or None,
            test_frameworks=frameworks,
        )
