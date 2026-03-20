"""Normalization & merge: standard CycloneDX BOM + AI-BOM.

Following the reference architecture Section D (Normalization & Entity Resolution)
and Section F (Output Assembly), this module merges:

1. A **standard CycloneDX BOM** (from ``CycloneDxGenerator``) containing
   packages, versions, licenses, and hashes.

2. An **AI-BOM** (from ``AiSbomExtractor``) containing agents, models, prompts,
   tools, datastores, and relationships.

Merge strategy
--------------
- Standard BOM is the **base**; its metadata, serialNumber, and spec fields
  are preserved.
- AI components are **overlaid**:
  - If an AI node maps to an existing standard dep component (same normalised
    package name), the dep component is **enriched** with ``aibom:*``
    properties rather than duplicated.
  - AI components with no matching dep are added as new components.
- CycloneDX ``dependencies`` edges from AI relationships are appended.
- ``metadata.properties`` receives an ``aibom:*`` summary block.

aibom:* property conventions (Appendix B of reference arch)
------------------------------------------------------------
- ``aibom:componentType``   — Xelo component type (AGENT, MODEL, etc.)
- ``aibom:agentFramework``  — framework adapter name (langgraph, crewai, …)
- ``aibom:promptHash``      — sha256 of prompt content (PROMPT nodes)
- ``aibom:toolRiskCategory``— risk category for TOOL nodes
- ``aibom:evidenceRef``     — ``file:startLine-endLine`` pointer
- ``aibom:confidence``      — extraction confidence (0–1)
- ``aibom:provider``        — LLM provider (MODEL nodes)
- ``aibom:modelFamily``     — model family label (MODEL nodes)
- ``aibom:modelCardUrl``    — model documentation URL (MODEL nodes)
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from .models import AiSbomDocument
from .types import ComponentType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERSION = "0.2.0"

# Map Xelo types to CycloneDX component types
_CDX_TYPE: dict[ComponentType, str] = {
    ComponentType.AGENT: "application",
    ComponentType.FRAMEWORK: "application",
    ComponentType.MODEL: "machine-learning-model",
    ComponentType.PROMPT: "data",
    ComponentType.DATASTORE: "data",
    ComponentType.TOOL: "library",
    ComponentType.AUTH: "library",
    ComponentType.PRIVILEGE: "library",
    ComponentType.API_ENDPOINT: "library",
    ComponentType.DEPLOYMENT: "library",
}

_TOOL_RISK_KEYWORDS: dict[str, str] = {
    "filesystem": "filesystem",
    "file": "filesystem",
    "shell": "code-execution",
    "exec": "code-execution",
    "bash": "code-execution",
    "sql": "data-read/write",
    "database": "data-read/write",
    "db": "data-read/write",
    "http": "network",
    "request": "network",
    "web": "network",
    "email": "communication",
    "slack": "communication",
    "search": "data-read",
    "read": "data-read",
}


def _normalise_name(name: str) -> str:
    """PEP 503-style normalisation for dedup matching."""
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def _infer_tool_risk(name: str, description: str = "") -> str:
    text = (name + " " + description).lower()
    for kw, cat in _TOOL_RISK_KEYWORDS.items():
        if kw in text:
            return cat
    return "general"


def _prompt_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Merger
# ---------------------------------------------------------------------------


class AiBomMerger:
    """Merge a standard CycloneDX BOM with an AI-BOM extraction.

    Usage::

        merger = AiBomMerger()
        unified = merger.merge(standard_bom, ai_doc)
        # unified is a complete CycloneDX 1.6 dict
    """

    def merge(
        self,
        standard_bom: dict[str, Any],
        ai_doc: AiSbomDocument,
        generator_method: str = "unknown",
    ) -> dict[str, Any]:
        """Return a unified CycloneDX 1.6 BOM.

        Parameters
        ----------
        standard_bom:
            CycloneDX BOM dict from ``CycloneDxGenerator.generate()``.
        ai_doc:
            Extracted AI-BOM from ``AiSbomExtractor``.
        generator_method:
            Description of how the standard BOM was generated (for provenance).
        """
        # Copy the standard BOM as base (avoid mutating input)
        result = {k: v for k, v in standard_bom.items()}

        # ── Build lookup: normalised name → index in standard components ──
        std_components: list[dict[str, Any]] = list(result.get("components", []))
        name_index: dict[str, int] = {}
        for i, comp in enumerate(std_components):
            norm = _normalise_name(comp.get("name", ""))
            if norm:
                name_index[norm] = i

        # ── Build node-id → bom-ref map for edge resolution ─────────────
        id_to_ref: dict[str, str] = {str(node.id): str(node.id) for node in ai_doc.nodes}

        # ── Process each AI node ─────────────────────────────────────────
        ai_only_components: list[dict[str, Any]] = []

        for node in ai_doc.nodes:
            aibom_props = self._build_aibom_properties(node)
            cdx_type = _CDX_TYPE.get(node.component_type, "library")

            # Find matching standard dep component.
            # FRAMEWORK nodes use display names like "framework:langgraph" so also
            # try the adapter name.  For other types only match on the display name
            # to avoid false-positives (AGENT/PROMPT/TOOL nodes all carry adapter=
            # "langgraph" but should NOT be merged into the langgraph dep entry).
            extras = node.metadata.extras
            name_candidates = [node.name]
            if node.component_type == ComponentType.FRAMEWORK:
                name_candidates += [
                    extras.get("adapter", ""),
                    extras.get("framework", ""),
                ]
            std_idx: int | None = None
            for candidate in name_candidates:
                if candidate:
                    idx = name_index.get(_normalise_name(str(candidate)))
                    if idx is not None:
                        std_idx = idx
                        break

            if std_idx is not None:
                # Enrich the existing standard component in-place
                existing = std_components[std_idx]
                existing_props: list[dict[str, str]] = existing.get("properties", [])
                # Avoid duplicate aibom: properties on re-runs
                existing_aibom_names = {
                    p["name"] for p in existing_props if p["name"].startswith("aibom:")
                }
                for prop in aibom_props:
                    if prop["name"] not in existing_aibom_names:
                        existing_props.append(prop)
                existing["properties"] = existing_props
                # Upgrade type if the dep is actually an AI framework/model
                if cdx_type in {"application", "machine-learning-model", "data"}:
                    existing["type"] = cdx_type
                # Map bom-ref to the existing component's bom-ref for edge resolution
                existing_ref = existing.get("bom-ref", "")
                if existing_ref:
                    id_to_ref[str(node.id)] = existing_ref
            else:
                # Add as new AI-only component
                ai_comp: dict[str, Any] = {
                    "bom-ref": str(node.id),
                    "type": cdx_type,
                    "name": node.name,
                }
                if node.metadata.extras.get("version"):
                    ai_comp["version"] = str(node.metadata.extras["version"])
                if cdx_type == "machine-learning-model":
                    ext_refs: list[dict[str, str]] = []
                    if node.metadata.extras.get("model_card_url"):
                        ext_refs.append(
                            {
                                "type": "documentation",
                                "url": str(node.metadata.extras["model_card_url"]),
                                "comment": "Model card / provider documentation",
                            }
                        )
                    if node.metadata.extras.get("api_endpoint"):
                        ext_refs.append(
                            {
                                "type": "website",
                                "url": str(node.metadata.extras["api_endpoint"]),
                                "comment": "Provider API endpoint",
                            }
                        )
                    if ext_refs:
                        ai_comp["externalReferences"] = ext_refs
                ai_comp["properties"] = aibom_props
                ai_only_components.append(ai_comp)

        # ── Assemble final component list ────────────────────────────────
        result["components"] = std_components + ai_only_components

        # ── AI relationship edges → CycloneDX dependencies ───────────────
        existing_deps: list[dict[str, Any]] = list(result.get("dependencies", []))
        for edge in ai_doc.edges:
            src_ref = id_to_ref.get(str(edge.source))
            tgt_ref = id_to_ref.get(str(edge.target))
            if src_ref and tgt_ref and src_ref != tgt_ref:
                # Merge into existing entry for src_ref, or add new
                existing_entry = next((d for d in existing_deps if d.get("ref") == src_ref), None)
                if existing_entry:
                    if tgt_ref not in existing_entry.get("dependsOn", []):
                        existing_entry.setdefault("dependsOn", []).append(tgt_ref)
                else:
                    existing_deps.append({"ref": src_ref, "dependsOn": [tgt_ref]})
        result["dependencies"] = existing_deps

        # ── Metadata enrichment ───────────────────────────────────────────
        meta: dict[str, Any] = result.get("metadata", {})
        meta_props: list[dict[str, str]] = list(meta.get("properties", []))

        # Ensure tool entry records Xelo
        tools: list[dict[str, str]] = meta.get("tools", [])
        vela_tool = {"vendor": "Xelo", "name": "xelo", "version": _VERSION}
        if not any(t.get("name") == "xelo" for t in tools):
            tools.append(vela_tool)
        meta["tools"] = tools

        # AI-BOM summary properties
        ai_counts = self._count_by_type(ai_doc)
        summary_props: list[dict[str, str]] = [
            {"name": "aibom:version", "value": "1.0"},
            {"name": "aibom:generator", "value": f"xelo/{_VERSION}"},
            {"name": "aibom:depsBomMethod", "value": generator_method},
            {"name": "aibom:scanTarget", "value": ai_doc.target},
            {"name": "aibom:scanTimestamp", "value": datetime.now(timezone.utc).isoformat()},
            {"name": "aibom:aiComponentTotal", "value": str(len(ai_doc.nodes))},
            {"name": "aibom:aiRelationships", "value": str(len(ai_doc.edges))},
        ]
        for ctype, count in ai_counts.items():
            summary_props.append(
                {
                    "name": f"aibom:count:{ctype.lower()}",
                    "value": str(count),
                }
            )

        # Quality gate (Section 9 of reference arch)
        has_model = ai_counts.get("MODEL", 0) > 0
        has_agent = ai_counts.get("AGENT", 0) > 0
        all_have_evidence = all(
            node.metadata.extras.get("evidence_count", 0) > 0 for node in ai_doc.nodes
        )
        quality_pass = has_model or has_agent
        summary_props.append(
            {
                "name": "aibom:qualityGate",
                "value": "pass" if quality_pass else "warn",
            }
        )
        summary_props.append(
            {
                "name": "aibom:allNodesHaveEvidence",
                "value": str(all_have_evidence).lower(),
            }
        )

        # Confidence summary
        if ai_doc.nodes:
            confidences = [n.confidence for n in ai_doc.nodes]
            avg_conf = sum(confidences) / len(confidences)
            min_conf = min(confidences)
            summary_props.append(
                {
                    "name": "aibom:avgConfidence",
                    "value": f"{avg_conf:.2f}",
                }
            )
            summary_props.append(
                {
                    "name": "aibom:minConfidence",
                    "value": f"{min_conf:.2f}",
                }
            )

        # Remove any pre-existing aibom: props before adding fresh ones
        meta_props = [p for p in meta_props if not p["name"].startswith("aibom:")]
        meta["properties"] = meta_props + summary_props

        # Ensure timestamp is present
        if "timestamp" not in meta:
            meta["timestamp"] = datetime.now(timezone.utc).isoformat()

        result["metadata"] = meta
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_aibom_properties(self, node: Any) -> list[dict[str, str]]:
        """Build the ``aibom:*`` property list for a node."""
        extras = node.metadata.extras
        props: list[dict[str, str]] = [
            {"name": "aibom:componentType", "value": node.component_type.value},
            {"name": "aibom:confidence", "value": f"{node.confidence:.2f}"},
        ]

        # Evidence reference: first evidence item's location
        if extras.get("evidence_count", 0) > 0 and hasattr(node, "id"):
            # Find evidence in the doc (not directly on node in current model)
            pass
        # File/line from extras or use node name as fallback
        evidence_ref = extras.get("source_file") or extras.get("file_path", "")
        if evidence_ref:
            props.append({"name": "aibom:evidenceRef", "value": str(evidence_ref)})

        if extras.get("adapter"):
            props.append({"name": "aibom:agentFramework", "value": str(extras["adapter"])})

        if node.component_type == ComponentType.PROMPT:
            content = extras.get("content", "")
            if content:
                props.append({"name": "aibom:promptHash", "value": _prompt_hash(content)})

        if node.component_type == ComponentType.TOOL:
            risk_cat = _infer_tool_risk(node.name)
            props.append({"name": "aibom:toolRiskCategory", "value": risk_cat})

        if node.component_type == ComponentType.MODEL:
            if extras.get("provider"):
                props.append({"name": "aibom:provider", "value": str(extras["provider"])})
            if extras.get("model_family"):
                props.append({"name": "aibom:modelFamily", "value": str(extras["model_family"])})
            if extras.get("model_card_url"):
                props.append({"name": "aibom:modelCardUrl", "value": str(extras["model_card_url"])})

        return props

    def _count_by_type(self, doc: AiSbomDocument) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in doc.nodes:
            key = node.component_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts
