"""SPDX 3.0.1 JSON-LD export plugin.

Produces output aligned with the NuGuardAI SPDX 3.0.1 reference format:

  * ``"@context"`` is the SPDX 3.0.1 canonical URL string (+ xelo namespace)
  * ``"spdxId"`` keys with ``SPDXRef-`` prefix (human-readable IDs)
  * ``"type"`` keys with namespace-prefixed type names
  * Shared blank-node creationInfo (``_:creationinfo``)
  * Element order: actors → CI → license → SpdxDocument → components → SBOM → Relationships
  * ``SpdxDocument`` has only ``rootElement`` (pointing to the SBOM)
  * ``software_Sbom`` has ``element`` list + ``software_sbomType``
  * Packages use ``software_packageVersion``, ``releaseTime``, ``suppliedBy``
  * Relationship elements with ``completeness: "complete"``
  * xelo: extension properties for AI-security metadata
"""

from __future__ import annotations

import json
import logging
import re
from importlib import resources
from pathlib import Path
from typing import Any

from xelo.models import AiSbomDocument, Node
from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin
from xelo.types import ComponentType, RelationshipType

_log = logging.getLogger("toolbox.plugins.spdx")

# ── Constants ─────────────────────────────────────────────────────────────────

_SPDX3_SPEC_VERSION = "3.0.1"
_SPDX3_CONTEXT_URL = "https://spdx.org/rdf/3.0.1/spdx-context.jsonld"
_XELO_NS = "https://nuguard.ai/spdx-ext/xelo/1.0/"

# Namespace-prefixed type names
_TYPE_SPDX_DOC = "SpdxDocument"
_TYPE_SBOM = "software_Sbom"
_TYPE_AI_PKG = "ai_AIPackage"
_TYPE_PKG = "software_Package"
_TYPE_DATASET = "dataset_Dataset"
_TYPE_ORG = "Organization"
_TYPE_TOOL = "Tool"
_TYPE_AGENT = "SoftwareAgent"
_TYPE_LICENSE = "simplelicensing_LicenseExpression"
_TYPE_REL = "Relationship"

# software_primaryPurpose vocab values
_PURPOSE: dict[ComponentType, str] = {
    ComponentType.MODEL: "model",
    ComponentType.AGENT: "application",
    ComponentType.FRAMEWORK: "framework",
    ComponentType.DATASTORE: "data",
    ComponentType.CONTAINER_IMAGE: "container",
    ComponentType.DEPLOYMENT: "application",
    ComponentType.TOOL: "library",
    ComponentType.PROMPT: "data",
    ComponentType.GUARDRAIL: "library",
    ComponentType.AUTH: "library",
    ComponentType.PRIVILEGE: "library",
    ComponentType.IAM: "library",
    ComponentType.API_ENDPOINT: "library",
}

# ComponentType → SPDX 3 element type
_SPDX3_TYPES: dict[ComponentType, str] = {
    ComponentType.MODEL: _TYPE_AI_PKG,
    ComponentType.AGENT: _TYPE_AI_PKG,
    ComponentType.DATASTORE: _TYPE_DATASET,
    ComponentType.FRAMEWORK: _TYPE_PKG,
    ComponentType.CONTAINER_IMAGE: _TYPE_PKG,
    ComponentType.DEPLOYMENT: _TYPE_PKG,
    ComponentType.TOOL: _TYPE_PKG,
    ComponentType.PROMPT: _TYPE_PKG,
    ComponentType.GUARDRAIL: _TYPE_PKG,
    ComponentType.AUTH: _TYPE_PKG,
    ComponentType.PRIVILEGE: _TYPE_PKG,
    ComponentType.IAM: _TYPE_PKG,
    ComponentType.API_ENDPOINT: _TYPE_PKG,
}

# Xelo RelationshipType → SPDX 3 relationshipType camelCase string
_REL_TYPES: dict[RelationshipType, str] = {
    RelationshipType.USES: "dependsOn",
    RelationshipType.CALLS: "dependsOn",
    RelationshipType.ACCESSES: "dataFile",
    RelationshipType.DEPLOYS: "generates",
    RelationshipType.PROTECTS: "other",
}


def _safe_id(name: str) -> str:
    """Convert a name to a safe SPDXRef identifier segment."""
    return re.sub(r"[^A-Za-z0-9.\-]", "-", name).strip("-")


def _spdx_ref(prefix: str, name: str) -> str:
    return f"SPDXRef-{prefix}-{_safe_id(name)}"


# ── Public plugin class ───────────────────────────────────────────────────────


class SpdxExporter(ToolPlugin):
    """Export an AiSbomDocument as SPDX 3.0.1 JSON-LD.

    Config keys
    -----------
    spec_version : str   SPDX spec version string (default "3.0.1")
    validate     : bool  Run SHACL validation after export (requires pyshacl)
    """

    name = "spdx_export"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        spec = config.get("spec_version", _SPDX3_SPEC_VERSION)
        doc = AiSbomDocument.model_validate(sbom)
        payload = _to_spdx3(doc, spec_version=spec)
        _log.info(
            "SPDX %s export: %d @graph elements from %d node(s)",
            spec,
            len(payload.get("@graph", [])),
            len(doc.nodes),
        )

        details: dict[str, Any] = dict(payload)

        if config.get("validate"):
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".spdx.json", delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(payload, tmp, indent=2)
                tmp_path = tmp.name
            try:
                conforms, report = validate_spdx3_jsonld(tmp_path)
                details["_xelo_validation"] = {"conforms": conforms, "report": report}
                _log.info("SHACL validation conforms=%s", conforms)
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        return ToolResult(
            status="ok",
            tool=self.name,
            message="SPDX 3 JSON-LD export generated",
            details=details,
        )


# ── Core converter ────────────────────────────────────────────────────────────


def _to_spdx3(doc: AiSbomDocument, *, spec_version: str = _SPDX3_SPEC_VERSION) -> dict[str, Any]:
    """Convert *doc* to SPDX 3.0.1 JSON-LD matching the NuGuardAI reference format."""
    return _convert_dict(doc, spec_version=spec_version)


def _convert_dict(
    doc: AiSbomDocument, *, spec_version: str = _SPDX3_SPEC_VERSION
) -> dict[str, Any]:
    """Build SPDX 3 JSON-LD following the NuGuardAI validator-hardened reference format."""
    created = doc.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    graph: list[dict[str, Any]] = []

    # Derive a human-readable project name from the target path/URL
    project_name = _project_slug(doc.target)
    ci_ref = "_:creationinfo"

    # Fixed spdxId values for document-level elements
    agent_id = "SPDXRef-Agent-XeloSoftwareAgent"
    tool_id = "SPDXRef-Tool-XeloGenerator"
    org_id = "SPDXRef-Org-NuGuardAI"
    license_id = "SPDXRef-License-DataLicenseCC0"
    doc_id = "SPDXRef-DOCUMENT"
    sbom_id = f"SPDXRef-SBOM-{_safe_id(project_name)}"

    # ── 1. Actors ───────────────────────────────────────────────────────────
    graph.append(
        {
            "type": _TYPE_AGENT,
            "spdxId": agent_id,
            "creationInfo": ci_ref,
            "name": "Xelo AI SBOM Generator Agent",
            "summary": "Software agent acting as the creator of this SPDX content.",
        }
    )
    graph.append(
        {
            "type": _TYPE_TOOL,
            "spdxId": tool_id,
            "creationInfo": ci_ref,
            "name": doc.generator,
            "summary": "Tool used to generate this SPDX content.",
        }
    )
    graph.append(
        {
            "type": _TYPE_ORG,
            "spdxId": org_id,
            "creationInfo": ci_ref,
            "name": "NuGuardAI",
        }
    )

    # ── 2. CreationInfo blank node ──────────────────────────────────────────
    graph.append(
        {
            "type": "CreationInfo",
            "@id": ci_ref,
            "specVersion": spec_version,
            "created": created,
            "createdBy": [agent_id],
            "createdUsing": [tool_id],
        }
    )

    # ── 3. Data license element ─────────────────────────────────────────────
    graph.append(
        {
            "type": _TYPE_LICENSE,
            "spdxId": license_id,
            "creationInfo": ci_ref,
            "name": "Data License CC0-1.0",
            "description": "License used for SPDX metadata in this document.",
            "simplelicensing_licenseExpression": "CC0-1.0",
        }
    )

    # ── 4. SpdxDocument ─────────────────────────────────────────────────────
    graph.append(
        {
            "type": _TYPE_SPDX_DOC,
            "spdxId": doc_id,
            "creationInfo": ci_ref,
            "name": f"AI SBOM for {doc.target}",
            "dataLicense": license_id,
            "profileConformance": ["core", "software", "ai", "simpleLicensing"],
            "rootElement": [sbom_id],
        }
    )

    # ── 5. Component elements ────────────────────────────────────────────────
    component_ids: list[str] = []
    rel_ids: list[str] = []
    supplier_orgs: dict[str, str] = {}  # name → spdxId

    for node in doc.nodes:
        elem, extra_orgs = _node_to_element(node, ci_ref, org_id, created, doc.target)
        graph.append(elem)
        component_ids.append(elem["spdxId"])
        supplier_orgs.update(extra_orgs)

    for dep in doc.deps:
        elem = _dep_to_element(dep, ci_ref, org_id, created, doc.target)
        graph.append(elem)
        component_ids.append(elem["spdxId"])

    # Add any supplier org elements discovered from node metadata
    for org_name, org_sid in supplier_orgs.items():
        if org_sid != org_id:
            graph.insert(
                3,
                {  # insert after NuGuardAI org
                    "type": _TYPE_ORG,
                    "spdxId": org_sid,
                    "creationInfo": ci_ref,
                    "name": org_name,
                },
            )

    # ── 6. software_Sbom ────────────────────────────────────────────────────
    sbom_elem: dict[str, Any] = {
        "type": _TYPE_SBOM,
        "spdxId": sbom_id,
        "creationInfo": ci_ref,
        "name": f"SBOM for {project_name}",
        "summary": f"AI SBOM for {doc.target} generated by xelo.",
        "software_sbomType": ["analyzed"],
        "rootElement": component_ids[:1] if component_ids else [],
        "element": list(component_ids),  # relationships added below
    }

    # Attach ScanSummary fields
    if doc.summary:
        s = doc.summary
        if s.frameworks:
            sbom_elem["xelo:frameworks"] = s.frameworks
        if s.modalities:
            sbom_elem["xelo:modalities"] = s.modalities
        if s.security_findings:
            sbom_elem["xelo:securityFindings"] = s.security_findings
        if s.deployment_platforms:
            sbom_elem["xelo:deploymentPlatforms"] = s.deployment_platforms
        if s.use_case:
            sbom_elem["xelo:useCase"] = s.use_case
        if s.node_counts:
            sbom_elem["xelo:nodeCountsByType"] = s.node_counts

    graph.append(sbom_elem)

    # ── 7. Relationship elements ─────────────────────────────────────────────
    # Build an id lookup from node uuid → spdxId
    id_map: dict[str, str] = {}
    for elem in graph:
        if elem.get("type") in (_TYPE_AI_PKG, _TYPE_PKG, _TYPE_DATASET) and "spdxId" in elem:
            # Reverse lookup: we stored the uuid in xelo:nodeId
            node_id_val = elem.get("xelo:nodeId")
            if node_id_val:
                id_map[node_id_val] = elem["spdxId"]

    for edge in doc.edges:
        src_id = id_map.get(str(edge.source))
        tgt_id = id_map.get(str(edge.target))
        if not src_id or not tgt_id:
            continue
        rel_type = _REL_TYPES.get(edge.relationship_type, "other")
        rel_sid = _spdx_ref("Relationship", f"{_safe_id(src_id)}-{rel_type}-{_safe_id(tgt_id)}")
        rel_elem = {
            "type": _TYPE_REL,
            "spdxId": rel_sid,
            "creationInfo": ci_ref,
            "relationshipType": rel_type,
            "from": src_id,
            "to": [tgt_id],
            "completeness": "complete",
        }
        graph.append(rel_elem)
        rel_ids.append(rel_sid)

    # Add relationship IDs to the Sbom element list
    if rel_ids:
        sbom_elem["element"] = list(component_ids) + rel_ids

    return {
        "@context": [_SPDX3_CONTEXT_URL, {"xelo": _XELO_NS}],
        "@graph": graph,
    }


# ── Node → SPDX element ───────────────────────────────────────────────────────


def _node_to_element(
    node: Node, ci_ref: str, default_org_id: str, created: str, repo_url: str = ""
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return ``(element_dict, {extra_org_name: extra_org_spdxId})``."""
    spdx_type = _SPDX3_TYPES.get(node.component_type, _TYPE_PKG)
    meta = node.metadata

    # Determine supplier
    provider = meta.extras.get("provider", "")
    extra_orgs: dict[str, str] = {}
    if provider and provider.lower() not in ("", "unknown"):
        supplier_id = _spdx_ref("Org", provider)
        if supplier_id != default_org_id:
            extra_orgs[provider] = supplier_id
        supplied_by = supplier_id
    else:
        supplied_by = default_org_id

    download_loc = meta.extras.get("source_url") or repo_url or "https://example.com"
    pkg_ver = meta.extras.get("version") or "unknown"
    purpose = _PURPOSE.get(node.component_type, "library")

    elem: dict[str, Any] = {
        "type": spdx_type,
        "spdxId": _spdx_ref(_elem_prefix(node.component_type), node.name),
        "creationInfo": ci_ref,
        "name": node.name,
        "software_downloadLocation": download_loc,
        "software_packageVersion": pkg_ver,
        "software_primaryPurpose": purpose,
        "releaseTime": created,
        "suppliedBy": supplied_by,
        # xelo:nodeId lets us resolve edges later
        "xelo:nodeId": str(node.id),
        "xelo:componentType": node.component_type.value,
        "xelo:confidence": node.confidence,
    }

    if node.component_type == ComponentType.MODEL:
        if meta.model_name:
            elem["ai_typeOfModel"] = [meta.model_name]
        if provider:
            elem["ai_domain"] = [provider]

    elif node.component_type == ComponentType.AGENT:
        elem["ai_autonomyType"] = "yes"
        if meta.framework:
            elem["xelo:framework"] = meta.framework

    elif node.component_type == ComponentType.DATASTORE:
        if meta.datastore_type:
            elem["dataset_datasetType"] = meta.datastore_type
            elem["xelo:datasetType"] = meta.datastore_type
        if meta.data_classification:
            elem["xelo:dataClassification"] = json.dumps(meta.data_classification)

    else:
        _add_package_extensions(elem, node)

    return elem, extra_orgs


def _elem_prefix(ctype: ComponentType) -> str:
    """Return the SPDXRef segment prefix for a ComponentType."""
    mapping = {
        ComponentType.MODEL: "Model",
        ComponentType.AGENT: "Agent",
        ComponentType.DATASTORE: "Dataset",
        ComponentType.FRAMEWORK: "Framework",
        ComponentType.TOOL: "Tool",
        ComponentType.GUARDRAIL: "Guardrail",
        ComponentType.AUTH: "Auth",
        ComponentType.PROMPT: "Prompt",
        ComponentType.DEPLOYMENT: "Deployment",
        ComponentType.CONTAINER_IMAGE: "Image",
        ComponentType.API_ENDPOINT: "API",
        ComponentType.PRIVILEGE: "Privilege",
        ComponentType.IAM: "IAM",
    }
    return mapping.get(ctype, "Package")


def _add_package_extensions(element: dict[str, Any], node: Node) -> None:
    meta = node.metadata
    if node.component_type == ComponentType.PROMPT and node.evidence:
        loc = node.evidence[0].location
        element["xelo:promptRef"] = f"{loc.path}:{loc.line or 0}"
    elif node.component_type == ComponentType.AUTH:
        if meta.auth_type:
            element["xelo:authType"] = meta.auth_type
        if meta.auth_class:
            element["xelo:authClass"] = meta.auth_class
    elif node.component_type == ComponentType.PRIVILEGE:
        if meta.privilege_scope:
            element["xelo:privilegeScope"] = meta.privilege_scope
    elif node.component_type == ComponentType.IAM:
        if meta.iam_type:
            element["xelo:iamType"] = meta.iam_type
        if meta.principal:
            element["xelo:principal"] = meta.principal
    elif node.component_type == ComponentType.API_ENDPOINT:
        if meta.endpoint:
            element["xelo:endpoint"] = meta.endpoint
        if meta.transport:
            element["xelo:transport"] = meta.transport
        if meta.method:
            element["xelo:method"] = meta.method
    elif node.component_type == ComponentType.DEPLOYMENT:
        if meta.deployment_target:
            element["xelo:deploymentTarget"] = meta.deployment_target
    elif node.component_type == ComponentType.CONTAINER_IMAGE:
        if meta.base_image:
            element["software_packageVersion"] = meta.base_image
        if meta.image_digest:
            element["xelo:imageDigest"] = meta.image_digest


def _dep_to_element(
    dep: Any, ci_ref: str, org_id: str, created: str, repo_url: str = ""
) -> dict[str, Any]:
    return {
        "type": _TYPE_PKG,
        "spdxId": _spdx_ref("Dep", dep.name),
        "creationInfo": ci_ref,
        "name": dep.name,
        "software_primaryPurpose": "library",
        "software_downloadLocation": repo_url or "https://example.com",
        "software_packageVersion": dep.version_spec or "unknown",
        "releaseTime": created,
        "suppliedBy": org_id,
        "externalIdentifier": [
            {
                "type": "ExternalIdentifier",
                "externalIdentifierType": "purl",
                "identifier": dep.purl,
            }
        ],
    }


def _project_slug(target: str) -> str:
    """Derive a short human-readable project name from a URL or path."""
    # Strip trailing slashes and .git
    t = target.rstrip("/").removesuffix(".git")
    # Last path segment
    return t.split("/")[-1] or target


# ── SHACL Validation ─────────────────────────────────────────────────────────


def validate_spdx3_jsonld(path: str) -> tuple[bool, str]:
    """Validate an SPDX 3 JSON-LD file against the bundled SPDX 3 SHACL shapes."""
    try:
        from pyshacl import validate as shacl_validate
        from rdflib import Graph
    except ImportError as ex:
        raise ImportError(
            "SHACL validation requires pyshacl and rdflib: pip install xelo[spdx]"
        ) from ex

    try:
        shacl_file = str(resources.files("spdx_tools.spdx3.writer.json_ld").joinpath("model.ttl"))
    except Exception as ex:  # noqa: BLE001
        raise FileNotFoundError(
            "SPDX 3 SHACL shapes (model.ttl) not found — install spdx-tools: pip install xelo[spdx]"
        ) from ex

    data_graph = Graph()
    with open(path, encoding="utf-8") as f:
        data_graph.parse(f, format="json-ld")

    shacl_graph = Graph()
    with open(shacl_file, encoding="utf-8") as f:
        shacl_graph.parse(f, format="ttl")

    conforms, _results_graph, results_text = shacl_validate(
        data_graph=data_graph,
        shacl_graph=shacl_graph,
        ont_graph=shacl_graph,
        inference="rdfs",
    )
    return bool(conforms), str(results_text)
