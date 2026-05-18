"""SBOM serializers: native JSON, CycloneDX 1.6, and CycloneDX 1.6 Extended.

``AiSbomSerializer`` converts an ``AiSbomDocument`` (and optional dependency list)
into the NuGuard-native JSON format, a standards-compliant CycloneDX 1.6 document,
or an extended CycloneDX 1.6 document that uses all AI-specific first-class fields
introduced in CycloneDX 1.6.

CycloneDX output structure (standard)
--------------------------------------
``metadata``
    Tool info and the scanned target component.
``components``
    Two groups merged into a single list:

    1. **AI components** (AGENT, MODEL, TOOL, PROMPT, DATASTORE, …) — extracted by
       NuGuard's framework adapters.  Mapped to CycloneDX ``type`` values:

       - AGENT / FRAMEWORK  → ``"application"``
       - MODEL              → ``"machine-learning-model"``
       - PROMPT             → ``"data"``
       - DATASTORE          → ``"data"``
       - everything else    → ``"library"``

    2. **Package dependencies** (optional ``PackageDep`` list from
       ``DependencyScanner``) — mapped to ``type: "library"`` with proper
       ``purl`` fields following the ``pkg:pypi/`` scheme.

``dependencies``
    Edges from the ``AiSbomDocument`` rendered as CycloneDX dependency refs.

CycloneDX Extended additions (``cyclonedx-ext``)
-------------------------------------------------
The extended format builds on the standard output and adds:

- ``modelCard`` on ``machine-learning-model`` components — CycloneDX 1.6 first-class
  field carrying model family, provider, quantisation, and risk considerations.
- ``data.contents`` / ``data.governance`` on ``data`` components — data
  classification labels, PII field inventory, retention policies.
- ``services`` top-level section — API_ENDPOINT nodes rendered as CycloneDX
  services with endpoint URLs, authentication flags, and data flows.
- ``compositions`` top-level section — full edge graph rendered as CycloneDX
  assembly compositions with aggregate relationship types.
- ``evidence.identity.occurrences`` on each component — source file locations
  where the component was detected.
- ``nuguard:*`` property namespace — richer set of properties including risk tags,
  tool permissions, access types, system-prompt excerpts (redacted), and framework
  adapter names.
"""

from __future__ import annotations

import json
import re
from typing import Any

from nuguard import __version__

from .deps import PackageDep
from .models import AiSbomDocument
from .types import ComponentType

# Maximum characters kept from a system prompt excerpt (rest is redacted).
_SYSTEM_PROMPT_MAX_CHARS = 200

# Regex that matches likely secrets / API keys in system prompt text.
_SECRET_RE = re.compile(
    r"(sk-|Bearer\s+|token\s*[:=]\s*|api[_-]?key\s*[:=]\s*|password\s*[:=]\s*)[^\s\"']{8,}",
    re.IGNORECASE,
)


def _redact_system_prompt(text: str) -> str:
    """Replace likely secret values with ``[REDACTED]``."""
    return _SECRET_RE.sub(r"\1[REDACTED]", text)


# CycloneDX 1.6 `compositions.aggregate` values for NuGuard relationship types.
_RELATIONSHIP_TO_CDX_AGGREGATE: dict[str, str] = {
    "CALLS": "internal",
    "ACCESSES": "internal",
    "USES": "internal",
    "DEPENDS_ON": "incomplete",
    "INSTANTIATES": "internal",
    "READS_FROM": "internal",
    "WRITES_TO": "internal",
    "CONFIGURED_BY": "incomplete",
    "ROUTES_TO": "internal",
    "EXPOSES": "unknown",
    "INCLUDES": "internal",
}


# CycloneDX component type mapping for AI node types
_AI_TYPE_MAP: dict[ComponentType, str] = {
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


class AiSbomSerializer:
    @staticmethod
    def to_json(doc: AiSbomDocument) -> str:
        """Serialise to native JSON (Pydantic schema)."""
        return doc.model_dump_json(indent=2, exclude_none=True)

    @staticmethod
    def to_dict(doc: AiSbomDocument) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return json.loads(doc.model_dump_json(exclude_none=True))

    @staticmethod
    def from_json(raw: str) -> AiSbomDocument:
        """Deserialise from a JSON string."""
        return AiSbomDocument.model_validate_json(raw)

    @staticmethod
    def to_cyclonedx(
        doc: AiSbomDocument,
        spec_version: str = "1.6",
        deps: list[PackageDep] | None = None,
    ) -> dict[str, Any]:
        """Build a CycloneDX 1.6 BOM dict.

        Parameters
        ----------
        doc:
            The extracted AI SBOM document.
        spec_version:
            CycloneDX spec version string (default ``"1.6"``).
        deps:
            Optional list of ``PackageDep`` objects from ``DependencyScanner``.
            When provided they are appended as ``library`` components with
            proper ``purl`` values.
        """
        # ── AI component section ──────────────────────────────────────
        ai_components: list[dict[str, Any]] = []
        for node in doc.nodes:
            cdx_type = _AI_TYPE_MAP.get(node.component_type, "library")
            extras = node.metadata.extras

            props: list[dict[str, str]] = [
                {"name": "xelo:component_type", "value": node.component_type.value},
                {"name": "xelo:confidence", "value": f"{node.confidence:.2f}"},
            ]
            if extras.get("adapter"):
                props.append({"name": "xelo:adapter", "value": str(extras["adapter"])})
            if extras.get("provider"):
                props.append({"name": "xelo:provider", "value": str(extras["provider"])})
            if extras.get("model_family"):
                props.append({"name": "xelo:model_family", "value": str(extras["model_family"])})
            dc = node.metadata.data_classification or extras.get("data_classification")
            if dc and isinstance(dc, list):
                props.append({"name": "xelo:data_classification", "value": ",".join(dc)})
            ct = node.metadata.classified_tables or extras.get("classified_tables")
            if ct and isinstance(ct, list):
                props.append({"name": "xelo:classified_tables", "value": ",".join(ct)})
            cf = node.metadata.classified_fields or extras.get("classified_fields")
            if cf and isinstance(cf, dict):
                # Compact representation: "table:field1,field2;table2:field3"
                cf_str = ";".join(
                    f"{tbl}:{','.join(flds) if isinstance(flds, list) else ','.join(sorted(flds))}"
                    for tbl, flds in sorted(cf.items())
                )
                props.append({"name": "xelo:classified_fields", "value": cf_str})

            component: dict[str, Any] = {
                "bom-ref": str(node.id),
                "type": cdx_type,
                "name": node.name,
            }
            if extras.get("version"):
                component["version"] = str(extras["version"])
            if cdx_type == "machine-learning-model" and extras.get("model_card_url"):
                component["externalReferences"] = [
                    {
                        "type": "documentation",
                        "url": str(extras["model_card_url"]),
                        "comment": "Model card / provider documentation",
                    }
                ]
            if extras.get("api_endpoint"):
                component.setdefault("externalReferences", []).append(
                    {
                        "type": "website",
                        "url": str(extras["api_endpoint"]),
                        "comment": "Provider API endpoint",
                    }
                )
            component["properties"] = props
            ai_components.append(component)

        # ── Dependency component section ──────────────────────────────
        # Use explicit deps when provided; fall back to doc.deps from the scan.
        effective_deps: list[PackageDep] = deps if deps is not None else doc.deps
        dep_components: list[dict[str, Any]] = []
        for dep in effective_deps:
            dep_entry: dict[str, Any] = {
                "bom-ref": dep.purl,
                "type": "library",
                "name": dep.name,
                "purl": dep.purl,
                "properties": [
                    {"name": "xelo:dep_group", "value": dep.group},
                    {"name": "xelo:source_file", "value": dep.source_file},
                ],
            }
            if dep.version:
                dep_entry["version"] = dep.version
            if dep.version_spec and dep.version_spec != f"=={dep.version}":
                dep_entry["properties"].append(
                    {"name": "xelo:version_spec", "value": dep.version_spec}
                )
            dep_components.append(dep_entry)

        # ── Edge → dependency refs ────────────────────────────────────
        dependencies: list[dict[str, Any]] = [
            {
                "ref": str(edge.source),
                "dependsOn": [str(edge.target)],
            }
            for edge in doc.edges
        ]

        return {
            "bomFormat": "CycloneDX",
            "specVersion": spec_version,
            "version": 1,
            "serialNumber": f"urn:uuid:{doc.schema_version}-{doc.generated_at.strftime('%Y%m%dT%H%M%SZ')}",
            "metadata": {
                "timestamp": doc.generated_at.isoformat(),
                "tools": [
                    {
                        "vendor": "Xelo",
                        "name": doc.generator,
                        "version": __version__,
                    }
                ],
                "component": {
                    "type": "application",
                    "name": doc.target,
                },
            },
            "components": ai_components + dep_components,
            "dependencies": dependencies,
        }

    @staticmethod
    def dump_cyclonedx_json(
        doc: AiSbomDocument,
        spec_version: str = "1.6",
        deps: list[PackageDep] | None = None,
    ) -> str:
        return json.dumps(
            AiSbomSerializer.to_cyclonedx(doc, spec_version=spec_version, deps=deps),
            indent=2,
        )

    # ------------------------------------------------------------------
    # CycloneDX Extended (cyclonedx-ext)
    # ------------------------------------------------------------------

    @staticmethod
    def to_cyclonedx_extended(
        doc: AiSbomDocument,
        spec_version: str = "1.6",
        deps: list[PackageDep] | None = None,
    ) -> dict[str, Any]:
        """Build a CycloneDX 1.6 Extended BOM dict.

        Produces the same base structure as :meth:`to_cyclonedx` and then
        enriches it with AI-specific first-class CycloneDX 1.6 fields:

        - ``modelCard`` on ML model components
        - ``data.contents`` / ``data.governance`` on datastore components
        - ``services`` section for API endpoint nodes
        - ``compositions`` section for the full edge graph
        - ``evidence.identity.occurrences`` for source-file provenance
        - ``nuguard:*`` property namespace with risk tags, tool permissions,
          access types, and (redacted) system-prompt excerpts
        """
        # Build a node id → node lookup for edge rendering
        node_by_id = {str(node.id): node for node in doc.nodes}

        effective_deps: list[PackageDep] = deps if deps is not None else doc.deps

        ai_components: list[dict[str, Any]] = []
        service_components: list[dict[str, Any]] = []

        for node in doc.nodes:
            cdx_type = _AI_TYPE_MAP.get(node.component_type, "library")
            extras = node.metadata.extras

            # ── nuguard:* properties ───────────────────────────────────
            props: list[dict[str, str]] = [
                {"name": "nuguard:component_type", "value": node.component_type.value},
                {"name": "nuguard:confidence", "value": f"{node.confidence:.2f}"},
            ]
            if extras.get("adapter"):
                props.append({"name": "nuguard:adapter", "value": str(extras["adapter"])})
            if extras.get("provider"):
                props.append({"name": "nuguard:provider", "value": str(extras["provider"])})
            if extras.get("model_family"):
                props.append({"name": "nuguard:model_family", "value": str(extras["model_family"])})
            if extras.get("framework"):
                props.append({"name": "nuguard:framework", "value": str(extras["framework"])})

            # Risk / security tags
            risk_tags: list[str] = extras.get("risk_tags") or []
            if risk_tags:
                props.append({"name": "nuguard:risk_tags", "value": ",".join(risk_tags)})

            # Tool permissions
            permissions: list[str] = extras.get("permissions") or []
            if permissions:
                props.append({"name": "nuguard:permissions", "value": ",".join(permissions)})

            # Access type (from incident edges — aggregate on the node)
            access_types: set[str] = set()
            for edge in doc.edges:
                if str(edge.target) == str(node.id) and edge.access_type:
                    access_types.add(edge.access_type.value)
            if access_types:
                props.append({"name": "nuguard:access_type", "value": ",".join(sorted(access_types))})

            # Data classification
            dc = node.metadata.data_classification or extras.get("data_classification")
            if dc and isinstance(dc, list):
                props.append({"name": "nuguard:data_classification", "value": ",".join(dc)})
            ct = node.metadata.classified_tables or extras.get("classified_tables")
            if ct and isinstance(ct, list):
                props.append({"name": "nuguard:classified_tables", "value": ",".join(ct)})
            cf = node.metadata.classified_fields or extras.get("classified_fields")
            if cf and isinstance(cf, dict):
                cf_str = ";".join(
                    f"{tbl}:{','.join(flds) if isinstance(flds, list) else ','.join(sorted(flds))}"
                    for tbl, flds in sorted(cf.items())
                )
                props.append({"name": "nuguard:classified_fields", "value": cf_str})

            # System prompt excerpt — redact sensitive-looking tokens
            system_prompt: str = extras.get("system_prompt") or ""
            if system_prompt:
                excerpt = _redact_system_prompt(system_prompt[:_SYSTEM_PROMPT_MAX_CHARS])
                if len(system_prompt) > _SYSTEM_PROMPT_MAX_CHARS:
                    excerpt += " … [truncated]"
                props.append({"name": "nuguard:system_prompt_excerpt", "value": excerpt})

            # Blocked topics from policy
            blocked_topics: list[str] = extras.get("blocked_topics") or []
            if blocked_topics:
                props.append({"name": "nuguard:blocked_topics", "value": ",".join(blocked_topics)})

            # ── Base component ─────────────────────────────────────────
            component: dict[str, Any] = {
                "bom-ref": str(node.id),
                "type": cdx_type,
                "name": node.name,
                "properties": props,
            }
            if extras.get("version"):
                component["version"] = str(extras["version"])

            # ── External references ────────────────────────────────────
            ext_refs: list[dict[str, str]] = []
            if cdx_type == "machine-learning-model" and extras.get("model_card_url"):
                ext_refs.append({
                    "type": "documentation",
                    "url": str(extras["model_card_url"]),
                    "comment": "Model card / provider documentation",
                })
            if extras.get("api_endpoint"):
                ext_refs.append({
                    "type": "website",
                    "url": str(extras["api_endpoint"]),
                    "comment": "Provider API endpoint",
                })
            if ext_refs:
                component["externalReferences"] = ext_refs

            # ── Evidence: source file occurrences ─────────────────────
            occurrences: list[dict[str, str]] = []
            source_path = extras.get("source_path") or extras.get("file_path")
            if source_path:
                occ: dict[str, str] = {"location": str(source_path)}
                line_no = extras.get("line_number") or extras.get("line")
                if line_no:
                    occ["line"] = str(line_no)
                occurrences.append(occ)
            for ev in (node.evidence or []):
                loc = ev.location
                if loc:
                    ev_occ: dict[str, str] = {"location": str(loc)}
                    if hasattr(loc, "line") and loc.line:
                        ev_occ["line"] = str(loc.line)
                    occurrences.append(ev_occ)
            if occurrences:
                component["evidence"] = {
                    "identity": {
                        "field": "name",
                        "confidence": round(node.confidence, 2),
                        "occurrences": occurrences,
                    }
                }

            # ── CycloneDX 1.6 first-class AI fields ───────────────────

            # modelCard — ML models
            if cdx_type == "machine-learning-model":
                model_card: dict[str, Any] = {}
                if extras.get("model_family") or extras.get("provider"):
                    model_card["modelParameters"] = {
                        "task": extras.get("use_case") or "text-generation",
                        "architectureFamily": extras.get("model_family") or "transformer",
                    }
                considerations: list[dict[str, Any]] = []
                if risk_tags:
                    considerations.append({
                        "type": "performance",
                        "description": f"Risk tags: {', '.join(risk_tags)}",
                    })
                if extras.get("system_prompt") or extras.get("system_prompt_excerpt"):
                    considerations.append({
                        "type": "environmental",
                        "description": "System prompt controls model behaviour — see nuguard:system_prompt_excerpt",
                    })
                if considerations:
                    model_card["considerations"] = considerations
                if model_card:
                    component["modelCard"] = model_card

            # data.contents / data.governance — datastores and prompts
            if cdx_type == "data":
                data_section: dict[str, Any] = {}
                if dc and isinstance(dc, list):
                    data_section["contents"] = {
                        "attachment": {
                            "contentType": "text/plain",
                            "content": f"data_classification:{','.join(dc)}",
                        }
                    }
                if ct and isinstance(ct, list):
                    data_section["governance"] = {
                        "custodians": [],
                        "stewards": [],
                        "owners": [],
                    }
                    # Encode the table inventory in a custom property rather than
                    # distorting the governance contacts schema.
                    props.append({"name": "nuguard:pii_tables", "value": ",".join(ct)})
                if data_section:
                    component["data"] = data_section

            # API_ENDPOINT nodes are rendered as CycloneDX services instead of
            # components so they show up in the services section with endpoints.
            if node.component_type == ComponentType.API_ENDPOINT:
                service: dict[str, Any] = {
                    "bom-ref": str(node.id),
                    "name": node.name,
                    "properties": props,
                }
                ep_url = extras.get("url") or extras.get("api_endpoint")
                if ep_url:
                    service["endpoints"] = [str(ep_url)]
                auth = extras.get("authenticated") or extras.get("auth_required")
                service["authenticated"] = bool(auth)
                method = extras.get("http_method") or extras.get("method")
                if method:
                    service["data"] = [{"flow": "inbound", "classification": str(method)}]
                service_components.append(service)
                continue  # don't add to ai_components

            ai_components.append(component)

        # ── Package dependency components ──────────────────────────────
        dep_components: list[dict[str, Any]] = []
        for dep in effective_deps:
            dep_entry: dict[str, Any] = {
                "bom-ref": dep.purl,
                "type": "library",
                "name": dep.name,
                "purl": dep.purl,
                "properties": [
                    {"name": "nuguard:dep_group", "value": dep.group},
                    {"name": "nuguard:source_file", "value": dep.source_file},
                ],
            }
            if dep.version:
                dep_entry["version"] = dep.version
            if dep.version_spec and dep.version_spec != f"=={dep.version}":
                dep_entry["properties"].append(
                    {"name": "nuguard:version_spec", "value": dep.version_spec}
                )
            dep_components.append(dep_entry)

        # ── Standard dependency refs ───────────────────────────────────
        dependencies: list[dict[str, Any]] = [
            {
                "ref": str(edge.source),
                "dependsOn": [str(edge.target)],
            }
            for edge in doc.edges
        ]

        # ── Compositions: full edge graph with relationship types ──────
        compositions: list[dict[str, Any]] = []
        for edge in doc.edges:
            src_node = node_by_id.get(str(edge.source))
            tgt_node = node_by_id.get(str(edge.target))
            # Map relationship type to CycloneDX aggregate type
            rel = edge.relationship_type.value if hasattr(edge.relationship_type, "value") else str(edge.relationship_type)
            agg_type = _RELATIONSHIP_TO_CDX_AGGREGATE.get(rel, "internal")
            assemblies: list[dict[str, str]] = [
                {"ref": str(edge.source)},
                {"ref": str(edge.target)},
            ]
            comp: dict[str, Any] = {
                "aggregate": agg_type,
                "assemblies": assemblies,
                "properties": [
                    {"name": "nuguard:relationship_type", "value": rel},
                ],
            }
            if edge.access_type:
                comp["properties"].append(
                    {"name": "nuguard:access_type", "value": edge.access_type.value}
                )
            if src_node and tgt_node:
                comp["properties"].append(
                    {"name": "nuguard:edge_label",
                     "value": f"{src_node.name} --[{rel}]--> {tgt_node.name}"}
                )
            compositions.append(comp)

        bom: dict[str, Any] = {
            "bomFormat": "CycloneDX",
            "specVersion": spec_version,
            "version": 1,
            "serialNumber": f"urn:uuid:{doc.schema_version}-{doc.generated_at.strftime('%Y%m%dT%H%M%SZ')}",
            "metadata": {
                "timestamp": doc.generated_at.isoformat(),
                "tools": [
                    {
                        "vendor": "NuGuard",
                        "name": doc.generator,
                        "version": "0.1.0",
                    }
                ],
                "component": {
                    "type": "application",
                    "name": doc.target,
                },
                "properties": [
                    {"name": "nuguard:sbom_schema_version", "value": doc.schema_version},
                    {"name": "nuguard:format", "value": "cyclonedx-ext"},
                ],
            },
            "components": ai_components + dep_components,
            "dependencies": dependencies,
        }
        if service_components:
            bom["services"] = service_components
        if compositions:
            bom["compositions"] = compositions
        return bom

    @staticmethod
    def dump_cyclonedx_ext_json(
        doc: AiSbomDocument,
        spec_version: str = "1.6",
        deps: list[PackageDep] | None = None,
    ) -> str:
        """Serialise to CycloneDX Extended JSON string."""
        return json.dumps(
            AiSbomSerializer.to_cyclonedx_extended(doc, spec_version=spec_version, deps=deps),
            indent=2,
        )
