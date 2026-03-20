"""AIBOM snapshot builder.

Converts an AiSbomDocument into a structured dict that compliance evaluators
can query without touching the Pydantic model directly.

Ported from assessment_service/core/aibom_snapshot_builder.py.
Key adaptations:
  - Takes AiSbomDocument directly (no DB/HTTP client).
  - Removes scan metadata block (no scan row available).
  - Node type comes from node.component_type (ComponentType enum).
  - Properties are read from node.metadata (NodeMetadata Pydantic model).
"""

from __future__ import annotations

from typing import Any

from nuguard.common.logging import get_logger
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType
from nuguard.policy.scoring import safe_float

_log = get_logger(__name__)

# Cap per node type bucket to prevent token explosion in downstream evaluators.
# Nodes are sorted by confidence descending before capping.
MAX_NODES_PER_BUCKET = 50

# Maximum characters from prompt content passed to evaluators.
MAX_PROMPT_CONTENT_CHARS = 1500


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------


def _meta(node: Node) -> dict[str, Any]:
    """Return a flat dict merging NodeMetadata fields and extras."""
    m = node.metadata
    base: dict[str, Any] = m.model_dump(exclude_none=True)
    extras: dict[str, Any] = base.pop("extras", {}) or {}
    # extras wins over base fields
    return {**base, **extras}


def _top_by_confidence(nodes: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(nodes) <= limit:
        return nodes
    return sorted(nodes, key=lambda n: safe_float(n.get("confidence"), 0.5), reverse=True)[:limit]


# ---------------------------------------------------------------------------
# Per-type detail extractors
# ---------------------------------------------------------------------------


def _prompt_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    content = str(p.get("content") or "")
    return {
        "id": str(node.id),
        "name": node.name,
        "file_path": str(p.get("file_path") or ""),
        "line": p.get("line_start") or p.get("line"),
        "content": content[:MAX_PROMPT_CONTENT_CHARS],
        "role": p.get("role"),
        "prompt_type": p.get("prompt_type"),
        "injection_risk_score": p.get("injection_risk_score"),
        "is_template": bool(p.get("is_template") or p.get("has_template_variables")),
        "template_variables": p.get("template_variables") or [],
        "confidence": node.confidence,
    }


def _model_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    return {
        "id": str(node.id),
        "name": node.name,
        "provider": p.get("provider"),
        "family": p.get("family"),
        "version": p.get("version") or p.get("base_version"),
        "model_card_url": p.get("model_card_url"),
        "model_name": node.metadata.model_name,
        "file_path": str(p.get("file_path") or ""),
        "line": p.get("line_start") or p.get("line"),
        "confidence": node.confidence,
    }


def _datastore_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    # data_classification may live in metadata.data_classification or extras
    dc = node.metadata.data_classification or p.get("data_classification") or []
    return {
        "id": str(node.id),
        "name": node.name,
        "provider": p.get("provider"),
        "datastore_type": node.metadata.datastore_type or p.get("datastore_type"),
        "subtype": p.get("subtype"),
        "data_classification": dc,
        "classified_fields": node.metadata.classified_fields or p.get("classified_fields") or [],
        "classified_tables": node.metadata.classified_tables or p.get("classified_tables") or [],
        "pii_detected": bool(dc),
        "has_ssl": p.get("has_ssl"),
        "username_detected": bool(p.get("username_detected")),
        "api_key_detected": bool(p.get("api_key_detected")),
        "credential_detected": bool(p.get("credential_detected")),
        "file_path": str(p.get("file_path") or p.get("source_file") or ""),
        "line": p.get("line_start") or p.get("source_line"),
        "confidence": node.confidence,
    }


def _tool_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    return {
        "id": str(node.id),
        "name": node.name,
        "tool_type": p.get("tool_type"),
        "executor_type": p.get("executor_type"),
        "runtime": p.get("runtime"),
        "has_api_schema": bool(p.get("has_api_schema")),
        "description": str(p.get("description") or "")[:200],
        "auth_type": node.metadata.auth_type,
        "has_auth": bool(node.metadata.auth_type),
        "is_external": bool(p.get("is_external") or p.get("external")),
        "file_path": str(p.get("file_path") or p.get("source_file") or ""),
        "line": p.get("line_start") or p.get("source_line"),
        "confidence": node.confidence,
    }


def _agent_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    return {
        "id": str(node.id),
        "name": node.name,
        "framework": node.metadata.framework or p.get("framework"),
        "subtype": p.get("subtype"),
        "foundation_model": p.get("foundation_model"),
        "has_instruction": bool(p.get("has_instruction")),
        "instruction_preview": str(p.get("instruction_preview") or "")[:300],
        "file_path": str(p.get("file_path") or p.get("source_file") or ""),
        "line": p.get("line_start") or p.get("source_line"),
        "confidence": node.confidence,
    }


def _guardrail_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    return {
        "id": str(node.id),
        "name": node.name,
        "provider": p.get("provider"),
        "verbs": p.get("verbs") or [],
        "api_groups": p.get("api_groups") or [],
        "sensitive_access": bool(p.get("sensitive_access")),
        "guardrail_type": p.get("guardrail_type"),
        "covers_input": bool(p.get("covers_input") or p.get("input_validation")),
        "covers_output": bool(p.get("covers_output") or p.get("output_validation")),
        "file_path": str(p.get("file_path") or ""),
        "line": p.get("line_start"),
        "confidence": node.confidence,
    }


def _auth_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    return {
        "id": str(node.id),
        "name": node.name,
        "auth_type": node.metadata.auth_type or p.get("auth_type"),
        "auth_class": node.metadata.auth_class or p.get("auth_class"),
        "file_path": str(p.get("file_path") or ""),
        "line": p.get("line_start"),
        "confidence": node.confidence,
    }


def _privilege_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    return {
        "id": str(node.id),
        "name": node.name,
        "node_type": node.component_type.value.lower(),
        "subtype": p.get("subtype") or "",
        "provider": p.get("provider"),
        "privilege_scope": node.metadata.privilege_scope or p.get("privilege_scope"),
        "purpose": p.get("purpose"),
        "privilege_level": p.get("privilege_level"),
        "has_wildcard": bool(p.get("has_wildcard")),
        "sensitive_resources": p.get("sensitive_resources") or [],
        "permissions": node.metadata.permissions or p.get("permissions") or [],
        "iam_type": node.metadata.iam_type,
        "file_path": str(p.get("file_path") or p.get("source_file") or ""),
        "line": p.get("line_start") or p.get("source_line"),
        "confidence": node.confidence,
    }


def _api_endpoint_detail(node: Node) -> dict[str, Any]:
    p = _meta(node)
    return {
        "id": str(node.id),
        "name": node.name,
        "endpoint": node.metadata.endpoint or p.get("endpoint"),
        "method": node.metadata.method or p.get("method"),
        "transport": node.metadata.transport or p.get("transport"),
        "rate_limit": p.get("rate_limit"),
        "has_auth": bool(node.metadata.auth_type or p.get("auth_type")),
        "file_path": str(p.get("file_path") or ""),
        "line": p.get("line_start"),
        "confidence": node.confidence,
    }


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

# Map ComponentType values to bucket keys
_TYPE_MAP: dict[str, str] = {
    ComponentType.PROMPT.value: "prompts",
    ComponentType.MODEL.value: "models",
    ComponentType.DATASTORE.value: "datastores",
    ComponentType.TOOL.value: "tools",
    ComponentType.GUARDRAIL.value: "guardrails",
    ComponentType.AUTH.value: "auth",
    ComponentType.PRIVILEGE.value: "privilege",
    ComponentType.IAM.value: "privilege",
    ComponentType.AGENT.value: "agents",
    ComponentType.FRAMEWORK.value: "agents",
    ComponentType.API_ENDPOINT.value: "api_endpoints",
    ComponentType.DEPLOYMENT.value: "deployments",
    ComponentType.CONTAINER_IMAGE.value: "deployments",
}

_EXTRACTORS = {
    "prompts": _prompt_detail,
    "models": _model_detail,
    "datastores": _datastore_detail,
    "tools": _tool_detail,
    "guardrails": _guardrail_detail,
    "auth": _auth_detail,
    "privilege": _privilege_detail,
    "agents": _agent_detail,
    "api_endpoints": _api_endpoint_detail,
}


def build_aibom_snapshot(doc: AiSbomDocument) -> dict[str, Any]:
    """Build a normalised assessment snapshot from an AiSbomDocument.

    The returned dict has three top-level keys:

    - ``counts``: integer aggregate counts per node category.
    - ``signals``: pre-computed boolean / numeric risk signals.
    - ``nodes``: per-type detail lists, each capped at MAX_NODES_PER_BUCKET
      and sorted by confidence descending.

    Args:
        doc: Fully populated AiSbomDocument.

    Returns:
        Snapshot dict suitable for passing to compliance evaluators.
    """
    # Partition nodes into typed buckets
    buckets: dict[str, list[Node]] = {k: [] for k in _EXTRACTORS}
    buckets["deployments"] = []

    for node in doc.nodes:
        bucket_key = _TYPE_MAP.get(node.component_type.value)
        if bucket_key is not None and bucket_key in buckets:
            buckets[bucket_key].append(node)

    # Build detail lists, capped by confidence rank
    node_details: dict[str, list[dict[str, Any]]] = {}
    for key, extractor in _EXTRACTORS.items():
        raw = [extractor(n) for n in buckets[key]]
        node_details[key] = _top_by_confidence(raw, MAX_NODES_PER_BUCKET)
    # Deployments have no dedicated extractor — store minimal info
    node_details["deployments"] = _top_by_confidence(
        [{"id": str(n.id), "name": n.name, "confidence": n.confidence} for n in buckets["deployments"]],
        MAX_NODES_PER_BUCKET,
    )

    # ---- Counts -----------------------------------------------------------
    counts: dict[str, int] = {
        "prompts": len(buckets["prompts"]),
        "models": len(buckets["models"]),
        "datastores": len(buckets["datastores"]),
        "tools": len(buckets["tools"]),
        "guardrails": len(buckets["guardrails"]),
        "auth": len(buckets["auth"]),
        "privilege": len(buckets["privilege"]),
        "agents": len(buckets["agents"]),
        "api_endpoints": len(buckets["api_endpoints"]),
        "edges": len(doc.edges),
    }

    # ---- Pre-computed signals ---------------------------------------------
    pii_datastores = [
        d for d in node_details["datastores"] if d.get("pii_detected")
    ]
    prompts_with_injection_risk = [
        p for p in node_details["prompts"]
        if safe_float(p.get("injection_risk_score"), 0.0) > 0.7
    ]
    tools_without_auth = [
        t for t in node_details["tools"] if not t.get("has_auth")
    ]
    external_tools = [
        t for t in node_details["tools"] if t.get("is_external")
    ]

    signals: dict[str, Any] = {
        "has_guardrail": len(buckets["guardrails"]) > 0,
        "has_auth": len(buckets["auth"]) > 0,
        "has_hitl_enforcement": any(
            "hitl" in str(g.get("guardrail_type") or "").lower()
            or "human" in g.get("name", "").lower()
            for g in node_details["guardrails"]
        ),
        "tools_without_auth_count": len(tools_without_auth),
        "datastores_with_pii_count": len(pii_datastores),
        "prompts_with_injection_risk": len(prompts_with_injection_risk),
        "external_tools_count": len(external_tools),
    }

    _log.debug(
        "aibom_snapshot built nodes=%d edges=%d",
        len(doc.nodes),
        len(doc.edges),
    )

    return {
        "counts": counts,
        "signals": signals,
        "nodes": node_details,
    }
