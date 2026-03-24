"""Automatic SBOM enrichment used by redteam runs.

This module intentionally has no user-facing mode switch yet.
Current behavior is fixed to "auto":
- Score baseline SBOM confidence.
- If confidence is low, enrich it with safe inferred metadata.
- Optionally probe a live target URL with a small, bounded request budget.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx

from nuguard.sbom.models import AiSbomDocument, Edge, Node
from nuguard.sbom.types import AccessType, ComponentType, RelationshipType

_log = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.65
_MAX_PROBE_REQUESTS = 6
_PROBE_TIMEOUT_SECONDS = 4.0
_MISSING_FIELD_RE = re.compile(
    r"missing\s+(?:\\?[\"'])?(?P<field>[A-Za-z0-9_\-]+)(?:\\?[\"'])?\s+field",
    re.IGNORECASE,
)


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
        or bool(ds.metadata.classified_fields)
        for ds in datastore_nodes
    )
    if sensitive_datastore_present:
        score += 0.15
    elif datastore_nodes:
        reasons.append("datastore sensitivity metadata missing")

    has_tool_access_edge = any(
        edge.relationship_type == RelationshipType.ACCESSES
        and _node_by_id(sbom, edge.source)
        and _node_by_id(sbom, edge.source).component_type == ComponentType.TOOL
        for edge in sbom.edges
    )
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


def _infer_required_field_from_error(body_text: str) -> str | None:
    if not body_text:
        return None

    # Prefer structured extraction when the response body is JSON.
    msg = body_text
    try:
        parsed = json.loads(body_text)
        if isinstance(parsed, dict):
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
    api_nodes = [n for n in enriched.nodes if n.component_type == ComponentType.API_ENDPOINT]

    if not agent_nodes and (tool_nodes or model_nodes):
        desc = ""
        if enriched.summary and enriched.summary.use_case:
            desc = enriched.summary.use_case
        enriched.nodes.append(
            Node(
                name=_safe_name_from_target(enriched.target),
                component_type=ComponentType.AGENT,
                confidence=0.55,
                metadata={
                    "description": desc or "Inferred conversational assistant from runtime/security graph.",
                    "extras": {"source": "auto_enrichment"},
                },
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
                metadata={
                    "endpoint": path,
                    "method": method,
                    "accepts_user_input": method == "POST",
                    "extras": {"source": "auto_enrichment"},
                },
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

    # Derive flat pii/phi fields from classified fields when absent.
    for ds in enriched.nodes:
        if ds.component_type != ComponentType.DATASTORE:
            continue
        flat = _derive_sensitive_fields(ds.metadata.classified_fields)
        if flat and not ds.metadata.pii_fields:
            ds.metadata.pii_fields = flat
        if ds.metadata.phi_fields is None and ds.metadata.pii_fields is not None:
            ds.metadata.phi_fields = []

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
        has_tool_datastore_edge = any(
            e.relationship_type == RelationshipType.ACCESSES
            and _node_by_id(enriched, e.source)
            and _node_by_id(enriched, e.source).component_type == ComponentType.TOOL
            and _node_by_id(enriched, e.target)
            and _node_by_id(enriched, e.target).component_type == ComponentType.DATASTORE
            for e in enriched.edges
        )
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
    return candidates[:_MAX_PROBE_REQUESTS]


async def _probe_and_enrich(sbom: AiSbomDocument, target_url: str) -> tuple[AiSbomDocument, int]:
    """Best-effort bounded runtime probe to refine endpoint metadata."""
    enriched = sbom.model_copy(deep=True)
    probed = 0

    endpoint_by_path: dict[str, Node] = {}
    for node in enriched.nodes:
        if node.component_type == ComponentType.API_ENDPOINT and node.metadata.endpoint:
            endpoint_by_path[node.metadata.endpoint] = node

    candidates = _collect_probe_candidates(enriched)
    timeout = httpx.Timeout(_PROBE_TIMEOUT_SECONDS)

    async with httpx.AsyncClient(base_url=target_url.rstrip("/"), timeout=timeout, follow_redirects=True) as client:
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

            node = endpoint_by_path.get(path)
            if node is None and get_resp.status_code in (200, 401, 403, 405, 429):
                node = Node(
                    name=f"{path} API",
                    component_type=ComponentType.API_ENDPOINT,
                    confidence=0.60,
                    metadata={"endpoint": path, "extras": {"source": "runtime_probe"}},
                    evidence=[],
                )
                enriched.nodes.append(node)
                endpoint_by_path[path] = node

            if node is not None:
                if node.metadata.method is None:
                    node.metadata.method = "GET"
                if get_resp.status_code in (401, 403):
                    node.metadata.auth_required = True
                if get_resp.status_code == 429:
                    node.metadata.rate_limited = True

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

            node = endpoint_by_path.get(path)
            if node is None and post_resp.status_code in (200, 400, 401, 403, 405, 429):
                node = Node(
                    name=f"{path} API",
                    component_type=ComponentType.API_ENDPOINT,
                    confidence=0.62,
                    metadata={"endpoint": path, "extras": {"source": "runtime_probe"}},
                    evidence=[],
                )
                enriched.nodes.append(node)
                endpoint_by_path[path] = node

            if node is None:
                continue

            node.metadata.method = "POST"
            node.metadata.accepts_user_input = True
            inferred_key = _infer_required_field_from_error(post_resp.text or "")
            if inferred_key:
                node.metadata.chat_payload_key = inferred_key
            else:
                node.metadata.chat_payload_key = node.metadata.chat_payload_key or "message"
            if post_resp.status_code in (401, 403):
                node.metadata.auth_required = True
            if post_resp.status_code == 429:
                node.metadata.rate_limited = True

            if post_resp.headers.get("content-type", "").lower().startswith("application/json"):
                try:
                    body = post_resp.json()
                except Exception:
                    body = None
                if isinstance(body, dict):
                    for key in ("response", "content", "text"):
                        if key in body:
                            node.metadata.response_text_key = key
                            break

    _ensure_summary_node_counts(enriched)
    return enriched, probed


def _docs_equal(a: AiSbomDocument, b: AiSbomDocument) -> bool:
    return a.model_dump(mode="json") == b.model_dump(mode="json")


def _enriched_output_path(sbom_path: Path) -> Path:
    if sbom_path.suffix:
        return sbom_path.with_name(f"{sbom_path.stem}.enriched{sbom_path.suffix}")
    return sbom_path.with_name(f"{sbom_path.name}.enriched.json")


def _write_enriched(sbom: AiSbomDocument, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(sbom.model_dump(mode="json"), indent=2)
    out_path.write_text(payload + "\n")


async def maybe_auto_enrich_sbom(
    sbom: AiSbomDocument,
    sbom_path: Path | None,
    target_url: str | None,
) -> EnrichmentResult:
    """Perform default auto enrichment and return selected SBOM.

    No CLI options are currently exposed; behavior is fixed to auto mode.
    """
    confidence_before, reasons = _score_confidence(sbom)
    if confidence_before >= _CONFIDENCE_THRESHOLD:
        return EnrichmentResult(
            sbom=sbom,
            enriched=False,
            confidence_before=confidence_before,
            confidence_after=confidence_before,
            reasons=["confidence is already high"],
            probe_attempted=False,
            probe_requests=0,
            artifact_path=None,
        )

    enriched = _enrich_static(sbom)
    probe_requests = 0
    probe_attempted = False

    if target_url:
        probe_attempted = True
        try:
            enriched, probe_requests = await _probe_and_enrich(enriched, target_url)
        except Exception as exc:
            _log.warning("SBOM runtime probe failed: %s", exc)

    confidence_after, _ = _score_confidence(enriched)
    changed = not _docs_equal(sbom, enriched)
    artifact_path: Path | None = None

    if changed and sbom_path is not None:
        artifact_path = _enriched_output_path(sbom_path)
        try:
            _write_enriched(enriched, artifact_path)
        except Exception as exc:
            _log.warning("Could not write enriched SBOM artifact %s: %s", artifact_path, exc)
            artifact_path = None

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
