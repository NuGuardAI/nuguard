"""Automatic SBOM enrichment used by redteam runs.

This module intentionally has no user-facing mode switch yet.
Current behavior is fixed to "auto":
- Score baseline SBOM confidence.
- If confidence is low, enrich it with safe inferred metadata.
- Optionally probe a live target URL with a small, bounded request budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import AccessType, ComponentType, RelationshipType

_log = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.65
_MAX_PROBE_REQUESTS = 10
_PROBE_TIMEOUT_SECONDS = 4.0
_MISSING_FIELD_RE = re.compile(
    r"missing\s+(?:\\?[\"'])?(?P<field>[A-Za-z0-9_\-]+)(?:\\?[\"'])?\s+field",
    re.IGNORECASE,
)

_LOC_IGNORED_TOKENS = {
    "body",
    "query",
    "path",
    "header",
    "headers",
    "cookie",
    "cookies",
    "json",
}

_MISSING_DESCRIPTION_TOKENS = {"", "null", "none", "n/a", "na", "unknown"}


@dataclass
class EnrichmentResult:
    """Result of auto enrichment decision and processing."""

    sbom: AiSbomDocument
    enriched: bool
    confidence_before: float
    confidence_after: float
    reasons: list[str]
    probe_attempted: bool
    probe_requests: int
    artifact_path: Path | None


def _safe_name_from_target(target: str) -> str:
    stem = Path(target.rstrip("/")).name or "Application"
    stem = stem.replace("-", " ").replace("_", " ").strip()
    if not stem:
        stem = "Application"
    return f"{stem.title()} Assistant"


def _node_type_counts(sbom: AiSbomDocument) -> dict[ComponentType, int]:
    counts: dict[ComponentType, int] = {}
    for node in sbom.nodes:
        counts[node.component_type] = counts.get(node.component_type, 0) + 1
    return counts


def _score_confidence(sbom: AiSbomDocument) -> tuple[float, list[str]]:
    """Return confidence score [0,1] and explanation reasons."""
    score = 0.0
    reasons: list[str] = []

    counts = _node_type_counts(sbom)
    api_nodes = [n for n in sbom.nodes if n.component_type == ComponentType.API_ENDPOINT]
    tool_nodes = [n for n in sbom.nodes if n.component_type == ComponentType.TOOL]
    datastore_nodes = [n for n in sbom.nodes if n.component_type == ComponentType.DATASTORE]
    has_agent = counts.get(ComponentType.AGENT, 0) > 0

    if has_agent:
        score += 0.20
    else:
        reasons.append("missing AGENT node")

    api_count = len(api_nodes)
    if api_count >= 3:
        score += 0.15
    elif api_count >= 2:
        score += 0.08
        reasons.append("limited API endpoint coverage")
    else:
        reasons.append("insufficient API endpoint coverage")

    tool_count = len(tool_nodes)
    if tool_count >= 2:
        score += 0.15
    elif tool_count == 1:
        score += 0.08
        reasons.append("limited tool coverage")
    else:
        reasons.append("missing tool coverage")

    api_meta_present = sum(
        1
        for n in api_nodes
        if (n.metadata.endpoint and n.metadata.method)
    )
    api_meta_ratio = (api_meta_present / api_count) if api_count else 0.0
    score += 0.20 * api_meta_ratio
    if api_meta_ratio < 0.75:
        reasons.append("API metadata coverage is low")

    sensitive_datastore_present = any(
        bool(ds.metadata.pii_fields)
        or bool(ds.metadata.phi_fields)
        or bool(ds.metadata.pfi_fields)
        or bool(ds.metadata.classified_fields)
        for ds in datastore_nodes
    )
    if sensitive_datastore_present:
        score += 0.15
    elif datastore_nodes:
        reasons.append("datastore sensitivity metadata missing")

    def _is_tool_access_edge(edge: Edge) -> bool:
        if edge.relationship_type != RelationshipType.ACCESSES:
            return False
        src = _node_by_id(sbom, edge.source)
        return src is not None and src.component_type == ComponentType.TOOL

    has_tool_access_edge = any(_is_tool_access_edge(e) for e in sbom.edges)
    if has_tool_access_edge:
        score += 0.15
    elif tool_nodes and datastore_nodes:
        reasons.append("tool-to-datastore access edges missing")

    return min(1.0, score), reasons


def _node_by_id(sbom: AiSbomDocument, node_id: object) -> Node | None:
    node_id_str = str(node_id)
    for node in sbom.nodes:
        if str(node.id) == node_id_str:
            return node
    return None


def _has_edge(sbom: AiSbomDocument, source: Node, target: Node, rel: RelationshipType) -> bool:
    sid = str(source.id)
    tid = str(target.id)
    for edge in sbom.edges:
        if str(edge.source) == sid and str(edge.target) == tid and edge.relationship_type == rel:
            return True
    return False


def _existing_endpoint_paths(sbom: AiSbomDocument) -> set[str]:
    paths: set[str] = set()
    for node in sbom.nodes:
        if node.component_type != ComponentType.API_ENDPOINT:
            continue
        if node.metadata.endpoint:
            paths.add(node.metadata.endpoint)
    return paths


def _ensure_summary_node_counts(sbom: AiSbomDocument) -> None:
    if sbom.summary is None:
        return
    counts: dict[str, int] = {}
    for node in sbom.nodes:
        key = node.component_type.value
        counts[key] = counts.get(key, 0) + 1
    sbom.summary.node_counts = counts


def _derive_sensitive_fields(classified_fields: dict[str, list[str]] | None) -> list[str]:
    if not classified_fields:
        return []
    fields: list[str] = []
    for values in classified_fields.values():
        for name in values:
            if name not in fields:
                fields.append(name)
    return fields


# Keywords that identify a field as Personal Financial Information (PFI).
# Matched case-insensitively against field names in classified_fields.
_PFI_KEYWORDS = frozenset({
    "card_number", "card_num", "pan", "credit_card", "debit_card",
    "cvv", "cvc", "expiry", "expiration_date",
    "bank_account", "account_number", "account_num", "checking", "savings",
    "routing_number", "routing_num", "aba", "swift", "iban", "sort_code",
    "account_balance", "balance", "transaction", "wire_transfer", "ach",
    "ssn", "social_security", "tax_id", "ein", "tin", "itin",
    "payment_method", "payment_token", "stripe_customer", "plaid_token",
})


def _derive_pfi_fields(classified_fields: dict[str, list[str]] | None) -> list[str]:
    """Return field names from classified_fields that look like financial data (PFI).

    Matches against _PFI_KEYWORDS using exact and substring checks so that
    fields like 'stripe_customer_id' or 'routing_number_ach' are captured.
    """
    if not classified_fields:
        return []
    pfi: list[str] = []
    for values in classified_fields.values():
        for name in values:
            lower = name.lower().replace("-", "_").replace(" ", "_")
            if any(kw in lower for kw in _PFI_KEYWORDS) and name not in pfi:
                pfi.append(name)
    return pfi


def _normalize_description(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _MISSING_DESCRIPTION_TOKENS:
        return None
    return text or None


def _infer_field_from_loc(loc: Any) -> str | None:
    if not isinstance(loc, (list, tuple)):
        return None

    # FastAPI/Pydantic commonly reports loc like ["body", "user_query"].
    for token in reversed(loc):
        if not isinstance(token, str):
            continue
        candidate = token.strip()
        if not candidate:
            continue
        if candidate.lower() in _LOC_IGNORED_TOKENS:
            continue
        return candidate

    return None


def _ensure_extras(node: Node) -> dict[str, Any]:
    extras = node.metadata.extras
    if not isinstance(extras, dict):
        extras = {}
        node.metadata.extras = extras
    return extras


def _set_description_source(node: Node, source: str) -> None:
    extras = _ensure_extras(node)
    extras["description_source"] = source


def _node_description_prompt(node: Node) -> str:
    metadata = node.metadata
    context: dict[str, Any] = {
        "name": node.name,
        "component_type": node.component_type.value,
    }
    if metadata.endpoint:
        context["endpoint"] = metadata.endpoint
    if metadata.method:
        context["method"] = metadata.method
    if metadata.auth_required is not None:
        context["auth_required"] = metadata.auth_required
    if metadata.datastore_type:
        context["datastore_type"] = metadata.datastore_type
    if metadata.framework:
        context["framework"] = metadata.framework
    if metadata.model_name:
        context["model_name"] = metadata.model_name
    evidence_snippets = [e.detail for e in node.evidence[:3] if getattr(e, "detail", None)]
    if evidence_snippets:
        context["evidence"] = evidence_snippets

    return (
        "Write a single-sentence SBOM component description (max 25 words). "
        "Keep it factual and concise. Do not use markdown, bullets, prefixes, or quotes.\n"
        f"Component context: {json.dumps(context, ensure_ascii=True)}"
    )


async def _generate_description_with_llm(node: Node, llm_client: Any) -> str | None:
    try:
        response = await llm_client.complete(
            prompt=_node_description_prompt(node),
            system="You generate concise software SBOM component descriptions.",
            label=f"sbom-description:{node.component_type.value}",
            temperature=0,
        )
    except Exception:
        return None

    text = _normalize_description(response)
    if not text:
        return None
    if text.startswith("[NUGUARD_CANNED_RESPONSE]"):
        return None
    text = re.sub(r"\s+", " ", text).strip().strip('"\'')
    return _normalize_description(text)


async def _populate_node_descriptions(
    sbom: AiSbomDocument,
    *,
    llm_enabled: bool,
    llm_model: str | None,
    llm_api_key: str | None,
    llm_api_base: str | None = None,
) -> bool:
    llm_client: LLMClient | None = None
    if llm_enabled:
        try:
            from nuguard.common.llm_client import LLMClient  # noqa: PLC0415

            llm_client = LLMClient(model=llm_model or None, api_key=llm_api_key or None, api_base=llm_api_base or None)
        except Exception:
            llm_client = None

    # Nodes that need an LLM description call — collected for parallel dispatch
    llm_nodes: list[Node] = []
    for node in sbom.nodes:
        extras = _ensure_extras(node)
        is_auto_enriched = extras.get("source") == "auto_enrichment"
        existing = _normalize_description(node.metadata.description)
        if existing and not (llm_client is not None and is_auto_enriched):
            if node.metadata.description != existing:
                node.metadata.description = existing
            _set_description_source(node, "enriched_existing")
            continue
        source_desc = _normalize_description(extras.get("description"))
        if source_desc and llm_client is None:
            node.metadata.description = source_desc
            _set_description_source(node, "original_sbom")
            continue
        if llm_client is not None:
            llm_nodes.append(node)
            continue
        if source_desc:
            node.metadata.description = source_desc
            _set_description_source(node, "original_sbom")
            continue
        _set_description_source(node, "none")

    if not llm_nodes:
        return False

    # Fire all LLM description calls in parallel — they are independent.
    results = await asyncio.gather(
        *(_generate_description_with_llm(n, llm_client) for n in llm_nodes),
        return_exceptions=True,
    )
    changed = False
    for node, generated in zip(llm_nodes, results):
        extras = _ensure_extras(node)
        source_desc = _normalize_description(extras.get("description"))
        if isinstance(generated, str) and generated:
            node.metadata.description = generated
            _set_description_source(node, "llm_generated")
            changed = True
        elif source_desc:
            node.metadata.description = source_desc
            _set_description_source(node, "original_sbom")
            changed = True
        else:
            _set_description_source(node, "none")

    return changed


def _infer_required_field_from_error(body_text: str) -> str | None:
    if not body_text:
        return None

    # Prefer structured extraction when the response body is JSON.
    msg = body_text
    try:
        parsed = json.loads(body_text)
        if isinstance(parsed, dict):
            detail = parsed.get("detail")

            if isinstance(detail, list):
                for item in detail:
                    if not isinstance(item, dict):
                        continue

                    from_loc = _infer_field_from_loc(item.get("loc"))
                    if from_loc:
                        return from_loc

                    item_msg = item.get("msg")
                    if isinstance(item_msg, str):
                        m_item = _MISSING_FIELD_RE.search(item_msg)
                        if m_item:
                            return m_item.group("field").strip() or None

            if isinstance(detail, dict):
                from_loc = _infer_field_from_loc(detail.get("loc"))
                if from_loc:
                    return from_loc

                item_msg = detail.get("msg")
                if isinstance(item_msg, str):
                    m_item = _MISSING_FIELD_RE.search(item_msg)
                    if m_item:
                        return m_item.group("field").strip() or None

            for key in ("error", "message", "detail"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    msg = value
                    break
    except Exception:
        pass

    m = _MISSING_FIELD_RE.search(msg)
    if not m:
        m = _MISSING_FIELD_RE.search(body_text)
    if not m:
        return None
    field = m.group("field").strip()
    return field or None


def _enrich_static(sbom: AiSbomDocument) -> AiSbomDocument:
    enriched = sbom.model_copy(deep=True)

    agent_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.AGENT]
    model_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.MODEL]
    tool_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.TOOL]
    datastore_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.DATASTORE]

    if not agent_nodes and (tool_nodes or model_nodes):
        desc = ""
        if enriched.summary and enriched.summary.use_case:
            desc = enriched.summary.use_case
        enriched.nodes.append(
            Node(
                name=_safe_name_from_target(enriched.target),
                component_type=ComponentType.AGENT,
                confidence=0.55,
                metadata=NodeMetadata(
                    description=desc or "",
                    extras={"source": "auto_enrichment"},
                ),
                evidence=[],
            )
        )

    # Add endpoint nodes from summary if they are not represented in API nodes.
    summary_paths: Iterable[str] = enriched.summary.api_endpoints if enriched.summary else []
    existing_paths = _existing_endpoint_paths(enriched)
    for path in summary_paths:
        if not path or path in existing_paths:
            continue
        method = "POST" if "chat" in path.lower() or "message" in path.lower() else "GET"
        enriched.nodes.append(
            Node(
                name=f"{path} API",
                component_type=ComponentType.API_ENDPOINT,
                confidence=0.55,
                metadata=NodeMetadata(
                    endpoint=path,
                    method=method,
                    accepts_user_input=(method == "POST"),
                    extras={"source": "auto_enrichment"},
                ),
                evidence=[],
            )
        )
        existing_paths.add(path)

    # Fill common API metadata gaps in-place.
    for node in enriched.nodes:
        if node.component_type != ComponentType.API_ENDPOINT:
            continue
        name = node.name.lower()
        if not node.metadata.endpoint:
            if "health" in name:
                node.metadata.endpoint = "/health"
            elif "chat" in name and "message" in name:
                node.metadata.endpoint = "/chat/message"
        if not node.metadata.method:
            if node.metadata.endpoint and ("chat" in node.metadata.endpoint or "message" in node.metadata.endpoint):
                node.metadata.method = "POST"
            elif node.metadata.endpoint == "/health":
                node.metadata.method = "GET"
        if node.metadata.endpoint and "chat" in node.metadata.endpoint and not node.metadata.chat_payload_key:
            node.metadata.chat_payload_key = "message"
            node.metadata.response_text_key = "response"
            node.metadata.accepts_user_input = True

    # Derive flat pii/phi/pfi fields from classified fields when absent.
    for ds in enriched.nodes:
        if ds.component_type != ComponentType.DATASTORE:
            continue
        flat = _derive_sensitive_fields(ds.metadata.classified_fields)
        if flat and not ds.metadata.pii_fields:
            ds.metadata.pii_fields = flat
        if ds.metadata.phi_fields is None and ds.metadata.pii_fields is not None:
            ds.metadata.phi_fields = []
        # PFI: derive from classified_fields using financial keyword matching.
        if ds.metadata.pfi_fields is None:
            pfi = _derive_pfi_fields(ds.metadata.classified_fields)
            ds.metadata.pfi_fields = pfi if pfi else []

    # Add AGENT -> MODEL and AGENT -> TOOL edges if absent.
    agent_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.AGENT]
    model_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.MODEL]
    tool_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.TOOL]
    if agent_nodes:
        primary_agent = agent_nodes[0]
        if model_nodes and not _has_edge(enriched, primary_agent, model_nodes[0], RelationshipType.USES):
            enriched.edges.append(
                Edge(source=primary_agent.id, target=model_nodes[0].id, relationship_type=RelationshipType.USES)
            )
        for tool in tool_nodes:
            if not _has_edge(enriched, primary_agent, tool, RelationshipType.CALLS):
                enriched.edges.append(
                    Edge(source=primary_agent.id, target=tool.id, relationship_type=RelationshipType.CALLS)
                )

    # Add TOOL -> DATASTORE relation in the simplest single-datastore case if none exist.
    if datastore_nodes:
        def _is_tool_datastore_edge(e: Edge) -> bool:
            if e.relationship_type != RelationshipType.ACCESSES:
                return False
            src = _node_by_id(enriched, e.source)
            tgt = _node_by_id(enriched, e.target)
            return (
                src is not None
                and src.component_type == ComponentType.TOOL
                and tgt is not None
                and tgt.component_type == ComponentType.DATASTORE
            )

        has_tool_datastore_edge = any(_is_tool_datastore_edge(e) for e in enriched.edges)
        if not has_tool_datastore_edge and tool_nodes:
            enriched.edges.append(
                Edge(
                    source=tool_nodes[0].id,
                    target=datastore_nodes[0].id,
                    relationship_type=RelationshipType.ACCESSES,
                    access_type=AccessType.READWRITE,
                )
            )

    _ensure_summary_node_counts(enriched)
    return enriched


def _collect_probe_candidates(sbom: AiSbomDocument) -> list[str]:
    candidates: list[str] = []
    for node in sbom.nodes:
        if node.component_type == ComponentType.API_ENDPOINT and node.metadata.endpoint:
            if node.metadata.endpoint not in candidates:
                candidates.append(node.metadata.endpoint)
    if sbom.summary:
        for path in sbom.summary.api_endpoints:
            if path and path not in candidates:
                candidates.append(path)
    for fallback in ("/health", "/chat", "/chat/message"):
        if fallback not in candidates:
            candidates.append(fallback)
    # For each non-/api/ path, also try /api/{path} — handles Azure SWA and
    # similar platforms where backend routes are always under an /api/ prefix.
    extras: list[str] = []
    for path in list(candidates):
        if not path.startswith("/api/"):
            api_path = "/api" + path
            if api_path not in candidates and api_path not in extras:
                extras.append(api_path)
    candidates.extend(extras)
    return candidates[:_MAX_PROBE_REQUESTS]


_PROBE_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL


def _probe_node_id(endpoint: str) -> uuid.UUID:
    """Deterministic UUID for a probe-derived node, stable across re-runs."""
    return uuid.uuid5(_PROBE_NS, f"runtime_probe:{endpoint}")


async def _probe_and_enrich(
    sbom: AiSbomDocument,
    target_url: str,
    probe_auth_header: str | None = None,
) -> tuple[AiSbomDocument, int]:
    """Best-effort bounded runtime probe to refine endpoint metadata."""
    enriched = sbom.model_copy(deep=True)
    probed = 0

    endpoint_by_path: dict[str, Node] = {}
    for node in enriched.nodes:
        if node.component_type == ComponentType.API_ENDPOINT and node.metadata.endpoint:
            endpoint_by_path[node.metadata.endpoint] = node

    candidates = _collect_probe_candidates(enriched)
    timeout = httpx.Timeout(_PROBE_TIMEOUT_SECONDS)

    default_headers: dict[str, str] = {}
    if probe_auth_header:
        # Parse "Header-Name: value" format into a dict entry for httpx.
        if ": " in probe_auth_header:
            h_name, h_value = probe_auth_header.split(": ", 1)
            default_headers[h_name] = h_value

    async with httpx.AsyncClient(
        base_url=target_url.rstrip("/"),
        timeout=timeout,
        follow_redirects=True,
        headers=default_headers,
    ) as client:
        for path in candidates:
            if probed >= _MAX_PROBE_REQUESTS:
                break
            if not path.startswith("/"):
                continue

            # GET probe
            probed += 1
            try:
                get_resp = await client.get(path)
            except Exception:
                continue

            probe_node: Node | None = endpoint_by_path.get(path)
            if probe_node is None and get_resp.status_code in (200, 401, 403, 405, 429):
                probe_node = Node(
                    id=_probe_node_id(path),
                    name=f"{path} API",
                    component_type=ComponentType.API_ENDPOINT,
                    confidence=0.60,
                    metadata=NodeMetadata(endpoint=path, extras={"source": "runtime_probe"}),
                    evidence=[],
                )
                enriched.nodes.append(probe_node)
                endpoint_by_path[path] = probe_node

            if probe_node is not None:
                if probe_node.metadata.method is None:
                    probe_node.metadata.method = "GET"
                if get_resp.status_code in (401, 403):
                    probe_node.metadata.auth_required = True
                if get_resp.status_code == 429:
                    probe_node.metadata.rate_limited = True
                # 404 on GET means the static route doesn't exist — mark so
                # _discover_chat_config can deprioritise this node.
                if get_resp.status_code == 404:
                    extras = probe_node.metadata.extras or {}
                    extras["probe_get_404"] = True
                    probe_node.metadata.extras = extras

            # POST probe for chat-like paths only.
            if "chat" not in path and "message" not in path:
                continue
            if probed >= _MAX_PROBE_REQUESTS:
                break

            probed += 1
            try:
                post_resp = await client.post(path, json={"message": "hello"})
            except Exception:
                continue

            probe_node = endpoint_by_path.get(path)
            # Accept 500 on chat paths: it means the route exists but an upstream
            # dependency (e.g. OpenAI API key) is missing. Do NOT accept 500 for
            # the non-chat GET probe above — there it likely means a proper failure.
            chat_post_success_codes = (200, 400, 401, 403, 405, 429, 500)
            if probe_node is None and post_resp.status_code in chat_post_success_codes:
                probe_node = Node(
                    id=_probe_node_id(path),
                    name=f"{path} API",
                    component_type=ComponentType.API_ENDPOINT,
                    confidence=0.62,
                    metadata=NodeMetadata(endpoint=path, extras={"source": "runtime_probe"}),
                    evidence=[],
                )
                enriched.nodes.append(probe_node)
                endpoint_by_path[path] = probe_node

            if probe_node is None:
                continue

            probe_node.metadata.method = "POST"
            probe_node.metadata.accepts_user_input = True
            # 405 on POST confirms route exists but requires a different method;
            # mark so discovery can skip this as a chat endpoint fallback.
            if post_resp.status_code == 405:
                extras = probe_node.metadata.extras or {}
                extras["probe_post_405"] = True
                probe_node.metadata.extras = extras
            inferred_key = _infer_required_field_from_error(post_resp.text or "")
            if inferred_key:
                probe_node.metadata.chat_payload_key = inferred_key
            else:
                probe_node.metadata.chat_payload_key = probe_node.metadata.chat_payload_key or "message"
            if post_resp.status_code in (401, 403):
                probe_node.metadata.auth_required = True
            if post_resp.status_code == 429:
                probe_node.metadata.rate_limited = True

            if post_resp.headers.get("content-type", "").lower().startswith("application/json"):
                try:
                    body = post_resp.json()
                except Exception:
                    body = None
                if isinstance(body, dict):
                    for key in ("response", "content", "text"):
                        if key in body:
                            probe_node.metadata.response_text_key = key
                            break

    # Wire the synthesized AGENT node from the best probed chat endpoint.
    # This lets the behavior runner know how to talk to the agent directly.
    agent_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.AGENT]
    if agent_nodes:
        agent = agent_nodes[0]
        if not agent.metadata.endpoint:
            # Prefer /chat/message over /chat; both must be POST with chat_payload_key set.
            chat_probe: Node | None = None
            for preferred in ("/chat/message", "/chat"):
                candidate = endpoint_by_path.get(preferred)
                if (
                    candidate is not None
                    and candidate.metadata.method == "POST"
                    and candidate.metadata.chat_payload_key
                ):
                    chat_probe = candidate
                    break
            if chat_probe is not None:
                agent.metadata.endpoint = chat_probe.metadata.endpoint
                agent.metadata.chat_payload_key = chat_probe.metadata.chat_payload_key
                agent.metadata.accepts_user_input = True
                if chat_probe.metadata.response_text_key:
                    agent.metadata.response_text_key = chat_probe.metadata.response_text_key
                if chat_probe.metadata.auth_required is not None:
                    agent.metadata.auth_required = chat_probe.metadata.auth_required
                _log.debug(
                    "Wired AGENT node '%s' → endpoint=%s chat_payload_key=%s",
                    agent.name,
                    agent.metadata.endpoint,
                    agent.metadata.chat_payload_key,
                )

    _ensure_summary_node_counts(enriched)
    return enriched, probed


def _docs_equal(a: AiSbomDocument, b: AiSbomDocument) -> bool:
    return a.model_dump(mode="json") == b.model_dump(mode="json")


def _enriched_output_path(sbom_path: Path) -> Path:
    if sbom_path.suffix:
        return sbom_path.with_name(f"{sbom_path.stem}.enriched{sbom_path.suffix}")
    return sbom_path.with_name(f"{sbom_path.name}.enriched.json")


def _enrichment_cache_key(
    sbom: AiSbomDocument,
    target_url: str | None,
    probe_auth_header: str | None,
) -> str:
    """Stable sha256 hash of (sbom content, target_url, probe_auth_header)."""
    h = hashlib.sha256(json.dumps(sbom.model_dump(mode="json"), sort_keys=True).encode())
    if target_url:
        h.update(target_url.encode())
    if probe_auth_header:
        h.update(probe_auth_header.encode())
    return h.hexdigest()


def _write_enriched(sbom: AiSbomDocument, out_path: Path, cache_key: str | None = None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = sbom.model_dump(mode="json", exclude_none=True)
    if cache_key is not None:
        data["_enrichment_cache_key"] = cache_key
    payload = json.dumps(data, indent=2)
    out_path.write_text(payload + "\n")


async def maybe_auto_enrich_sbom(
    sbom: AiSbomDocument,
    sbom_path: Path | None,
    target_url: str | None,
    llm_enabled: bool = False,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_api_base: str | None = None,
    probe_auth_header: str | None = None,
) -> EnrichmentResult:
    """Perform default auto enrichment and return selected SBOM.

    Pipeline (always): structural enrichment first (static + probe), then LLM
    description generation on all nodes including synthesized ones. When LLM is
    disabled, the hardcoded fallback descriptions from _enrich_static are kept.

    Results are cached on disk: if an enriched artifact already exists at the
    expected path and its embedded ``_enrichment_cache_key`` matches the sha256
    of (sbom content + target_url + probe_auth_header), the cached artifact is
    returned immediately without re-running the pipeline.
    """
    # --- Cache check ---
    cache_key: str | None = None
    if sbom_path is not None:
        cache_key = _enrichment_cache_key(sbom, target_url, probe_auth_header)
        artifact_path = _enriched_output_path(sbom_path)
        if artifact_path.exists():
            try:
                raw = json.loads(artifact_path.read_text())
                if raw.get("_enrichment_cache_key") == cache_key:
                    raw.pop("_enrichment_cache_key", None)
                    cached_sbom = AiSbomDocument.model_validate(raw)
                    _log.debug("SBOM enrichment cache hit for %s", sbom_path.name)
                    confidence_before, _ = _score_confidence(sbom)
                    confidence_after, _ = _score_confidence(cached_sbom)
                    return EnrichmentResult(
                        sbom=cached_sbom,
                        enriched=True,
                        confidence_before=confidence_before,
                        confidence_after=confidence_after,
                        reasons=["enrichment_cache_hit"],
                        probe_attempted=False,
                        probe_requests=0,
                        artifact_path=artifact_path,
                    )
            except Exception as exc:
                _log.debug("SBOM enrichment cache miss (load error): %s", exc)

    artifact_path = None  # type: ignore[assignment]
    confidence_before, reasons = _score_confidence(sbom)
    if confidence_before >= _CONFIDENCE_THRESHOLD:
        # High-confidence SBOM: only fill missing descriptions, no structural changes.
        description_changed = await _populate_node_descriptions(
            sbom,
            llm_enabled=llm_enabled,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_api_base=llm_api_base,
        )
        if description_changed and sbom_path is not None:
            artifact_path = _enriched_output_path(sbom_path)
            try:
                _write_enriched(sbom, artifact_path, cache_key=cache_key)
            except Exception as exc:
                _log.warning("Could not write enriched SBOM artifact %s: %s", artifact_path, exc)
                artifact_path = None  # type: ignore[assignment]

        return EnrichmentResult(
            sbom=sbom,
            enriched=description_changed,
            confidence_before=confidence_before,
            confidence_after=confidence_before,
            reasons=["confidence is already high"],
            probe_attempted=False,
            probe_requests=0,
            artifact_path=artifact_path,
        )

    # Low-confidence path: structural enrichment first, then descriptions.
    enriched = _enrich_static(sbom)
    probe_requests = 0
    probe_attempted = False

    if target_url:
        probe_attempted = True
        try:
            enriched, probe_requests = await _probe_and_enrich(
                enriched, target_url, probe_auth_header=probe_auth_header
            )
        except Exception as exc:
            _log.warning("SBOM runtime probe failed: %s", exc)

    # Populate descriptions after all nodes (including synthesized) are present.
    # LLM overrides auto_enrichment fallback descriptions; non-LLM keeps them.
    await _populate_node_descriptions(
        enriched,
        llm_enabled=llm_enabled,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_api_base=llm_api_base,
    )

    confidence_after, _ = _score_confidence(enriched)
    changed = not _docs_equal(sbom, enriched)

    if changed and sbom_path is not None:
        artifact_path = _enriched_output_path(sbom_path)
        try:
            _write_enriched(enriched, artifact_path, cache_key=cache_key)
        except Exception as exc:
            _log.warning("Could not write enriched SBOM artifact %s: %s", artifact_path, exc)
            artifact_path = None  # type: ignore[assignment]

    return EnrichmentResult(
        sbom=enriched if changed else sbom,
        enriched=changed,
        confidence_before=confidence_before,
        confidence_after=confidence_after,
        reasons=reasons,
        probe_attempted=probe_attempted,
        probe_requests=probe_requests,
        artifact_path=artifact_path,
    )
