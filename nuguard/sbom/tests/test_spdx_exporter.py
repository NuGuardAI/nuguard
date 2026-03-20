"""Tests for SPDX 3.0.1 JSON-LD export plugin — validator-hardened reference format.

Covers:
- Root document structure (@context list with xelo namespace, @graph)
- SPDXRef- prefixed spdxId values (human-readable segment)
- Shared blank-node creationInfo (_:creationinfo — lowercase)
- Element order: actors (SoftwareAgent, Tool, Org) → CI → License → SpdxDocument → components → Sbom → Relationships
- SoftwareAgent element as creator
- simplelicensing_LicenseExpression element for data license
- SpdxDocument: dataLicense = reference, rootElement only (no element list)
- software_Sbom: element list, software_sbomType, name, summary
- Package properties: software_packageVersion, releaseTime, suppliedBy
- Relationship elements from doc.edges with completeness="complete"
- xelo: extension properties
- PackageDep serialised as software_Package with purl
- Plugin .run() path via SpdxExporter
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from xelo.deps import PackageDep
from xelo.models import (
    AiSbomDocument,
    Edge,
    Node,
    NodeMetadata,
    ScanSummary,
)
from xelo.toolbox.plugins.spdx_exporter import SpdxExporter, _to_spdx3
from xelo.types import ComponentType, RelationshipType

# ── helpers ────────────────────────────────────────────────────────────────────

_FIXED_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_CI_BLANK_NODE = "_:creationinfo"  # lowercase — matches reference


def _make_node(
    ctype: ComponentType,
    name: str = "test-node",
    *,
    metadata: NodeMetadata | None = None,
) -> Node:
    return Node(
        id=uuid.uuid4(),
        name=name,
        component_type=ctype,
        confidence=0.75,
        metadata=metadata or NodeMetadata(),
    )


def _minimal_doc(
    *,
    nodes: list[Node] | None = None,
    deps: list[PackageDep] | None = None,
    edges: list[Edge] | None = None,
    summary: ScanSummary | None = None,
) -> dict[str, Any]:
    doc = AiSbomDocument(
        target="test-repo",
        nodes=nodes or [],
        deps=deps or [],
        edges=edges or [],
        summary=summary,
        generated_at=_FIXED_TS,
    )
    return _to_spdx3(doc)


def _graph(spdx: dict[str, Any]) -> list[dict[str, Any]]:
    return spdx["@graph"]


def _elements_of_type(spdx: dict[str, Any], type_str: str) -> list[dict[str, Any]]:
    return [e for e in _graph(spdx) if e.get("type") == type_str]


# ── @context ───────────────────────────────────────────────────────────────────


class TestContext:
    def test_context_is_list(self) -> None:
        spdx = _minimal_doc()
        assert isinstance(spdx["@context"], list)

    def test_context_contains_spdx_url(self) -> None:
        spdx = _minimal_doc()
        urls = [c for c in spdx["@context"] if isinstance(c, str)]
        assert any("spdx.org/rdf/3.0" in u for u in urls)

    def test_context_contains_xelo_namespace(self) -> None:
        spdx = _minimal_doc()
        ns_dicts = [c for c in spdx["@context"] if isinstance(c, dict)]
        assert any("xelo" in d for d in ns_dicts)


# ── @graph basics ─────────────────────────────────────────────────────────────


class TestGraphStructure:
    def test_graph_is_list(self) -> None:
        spdx = _minimal_doc()
        assert isinstance(spdx["@graph"], list)

    def test_exactly_one_spdx_document(self) -> None:
        spdx = _minimal_doc()
        docs = _elements_of_type(spdx, "SpdxDocument")
        assert len(docs) == 1

    def test_exactly_one_sbom(self) -> None:
        spdx = _minimal_doc()
        sboms = _elements_of_type(spdx, "software_Sbom")
        assert len(sboms) == 1

    def test_exactly_one_creation_info(self) -> None:
        spdx = _minimal_doc()
        cis = _elements_of_type(spdx, "CreationInfo")
        assert len(cis) == 1

    def test_creation_info_before_components(self) -> None:
        """CI comes early — after actors but before SpdxDocument and components."""
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        graph = _graph(spdx)
        ci_idx = next(i for i, e in enumerate(graph) if e.get("type") == "CreationInfo")
        component_types = {"ai_AIPackage", "software_Package", "dataset_Dataset"}
        component_indices = [i for i, e in enumerate(graph) if e.get("type") in component_types]
        if component_indices:
            assert ci_idx < min(component_indices), "CreationInfo must appear before components"

    def test_graph_has_software_agent(self) -> None:
        spdx = _minimal_doc()
        assert _elements_of_type(spdx, "SoftwareAgent"), "SoftwareAgent creator element is required"

    def test_graph_has_org_and_tool(self) -> None:
        spdx = _minimal_doc()
        assert _elements_of_type(spdx, "Organization")
        assert _elements_of_type(spdx, "Tool")

    def test_graph_has_license_element(self) -> None:
        spdx = _minimal_doc()
        licenses = _elements_of_type(spdx, "simplelicensing_LicenseExpression")
        assert licenses, "Data license simplelicensing_LicenseExpression is required"


# ── Key names: spdxId and type (not @id / @type) ──────────────────────────────


class TestKeyNames:
    def test_elements_use_spdxId_not_at_id(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        for elem in _graph(spdx):
            if elem.get("type") not in ("CreationInfo",):
                assert "spdxId" in elem, f"element missing spdxId: {elem.get('type')}"
                assert "@id" not in elem

    def test_elements_use_type_not_at_type(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        for elem in _graph(spdx):
            assert "type" in elem, "element missing type"
            assert "@type" not in elem

    def test_spdxId_starts_with_SPDXRef(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.AGENT)])
        for elem in _graph(spdx):
            if "spdxId" in elem:
                assert elem["spdxId"].startswith("SPDXRef-"), (
                    f"spdxId should use SPDXRef- prefix, got: {elem['spdxId']}"
                )


# ── Shared blank-node creationInfo ────────────────────────────────────────────


class TestCreationInfo:
    def test_ci_is_blank_node(self) -> None:
        spdx = _minimal_doc()
        ci = next(e for e in _graph(spdx) if e.get("type") == "CreationInfo")
        assert ci.get("@id") == _CI_BLANK_NODE

    def test_all_elements_reference_blank_node(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        skip = {"CreationInfo"}
        for elem in _graph(spdx):
            if elem.get("type") not in skip:
                assert elem.get("creationInfo") == _CI_BLANK_NODE, (
                    f"{elem.get('type')} has wrong creationInfo: {elem.get('creationInfo')}"
                )

    def test_ci_has_required_fields(self) -> None:
        spdx = _minimal_doc()
        ci = next(e for e in _graph(spdx) if e.get("type") == "CreationInfo")
        assert ci["specVersion"] == "3.0.1"
        assert ci["created"] == "2026-01-01T00:00:00Z"
        assert "createdBy" in ci
        assert "createdUsing" in ci

    def test_ci_created_by_software_agent(self) -> None:
        spdx = _minimal_doc()
        ci = next(e for e in _graph(spdx) if e.get("type") == "CreationInfo")
        agent = _elements_of_type(spdx, "SoftwareAgent")[0]
        assert agent["spdxId"] in ci["createdBy"]

    def test_ci_created_using_tool(self) -> None:
        spdx = _minimal_doc()
        ci = next(e for e in _graph(spdx) if e.get("type") == "CreationInfo")
        tool = _elements_of_type(spdx, "Tool")[0]
        assert tool["spdxId"] in ci["createdUsing"]


# ── Data license element ──────────────────────────────────────────────────────


class TestDataLicense:
    def test_license_element_exists(self) -> None:
        spdx = _minimal_doc()
        licenses = _elements_of_type(spdx, "simplelicensing_LicenseExpression")
        assert len(licenses) == 1

    def test_license_element_has_cc0(self) -> None:
        spdx = _minimal_doc()
        lic = _elements_of_type(spdx, "simplelicensing_LicenseExpression")[0]
        assert lic.get("simplelicensing_licenseExpression") == "CC0-1.0"

    def test_spdx_doc_data_license_refs_element(self) -> None:
        spdx = _minimal_doc()
        doc_el = next(e for e in _graph(spdx) if e.get("type") == "SpdxDocument")
        lic = _elements_of_type(spdx, "simplelicensing_LicenseExpression")[0]
        assert doc_el.get("dataLicense") == lic["spdxId"]


# ── SpdxDocument ──────────────────────────────────────────────────────────────


class TestSpdxDocument:
    def test_doc_name_contains_target(self) -> None:
        spdx = _minimal_doc()
        doc_el = next(e for e in _graph(spdx) if e.get("type") == "SpdxDocument")
        assert "test-repo" in doc_el["name"]

    def test_doc_has_root_element(self) -> None:
        spdx = _minimal_doc()
        doc_el = next(e for e in _graph(spdx) if e.get("type") == "SpdxDocument")
        assert isinstance(doc_el.get("rootElement"), list)
        assert len(doc_el["rootElement"]) == 1

    def test_doc_root_element_points_to_sbom(self) -> None:
        spdx = _minimal_doc()
        doc_el = next(e for e in _graph(spdx) if e.get("type") == "SpdxDocument")
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert sbom["spdxId"] in doc_el["rootElement"]

    def test_doc_has_no_element_list(self) -> None:
        """SpdxDocument should only have rootElement, not an element list."""
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        doc_el = next(e for e in _graph(spdx) if e.get("type") == "SpdxDocument")
        assert "element" not in doc_el, "SpdxDocument must NOT have an 'element' list"

    def test_doc_profile_conformance_includes_simple_licensing(self) -> None:
        spdx = _minimal_doc()
        doc_el = next(e for e in _graph(spdx) if e.get("type") == "SpdxDocument")
        assert "simpleLicensing" in doc_el.get("profileConformance", [])


# ── software_Sbom ─────────────────────────────────────────────────────────────


class TestSoftwareSbom:
    def test_sbom_has_element_list(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert isinstance(sbom.get("element"), list)
        assert len(sbom["element"]) >= 1

    def test_sbom_has_name(self) -> None:
        spdx = _minimal_doc()
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert "name" in sbom

    def test_sbom_has_summary(self) -> None:
        spdx = _minimal_doc()
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert "summary" in sbom

    def test_sbom_has_sbom_type(self) -> None:
        spdx = _minimal_doc()
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert sbom.get("software_sbomType") == ["analyzed"]

    def test_sbom_element_list_contains_components(self) -> None:
        node = _make_node(ComponentType.MODEL, "gpt-4")
        spdx = _minimal_doc(nodes=[node])
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        ai_pkgs = _elements_of_type(spdx, "ai_AIPackage")
        for pkg in ai_pkgs:
            assert pkg["spdxId"] in sbom["element"]


# ── Component type mapping ────────────────────────────────────────────────────


class TestComponentTypeMapping:
    def test_model_produces_ai_AIPackage(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        pkgs = _elements_of_type(spdx, "ai_AIPackage")
        assert pkgs, "MODEL should produce ai_AIPackage"

    def test_agent_produces_ai_AIPackage(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.AGENT)])
        pkgs = _elements_of_type(spdx, "ai_AIPackage")
        assert pkgs, "AGENT should produce ai_AIPackage"

    def test_datastore_produces_dataset_Dataset(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.DATASTORE)])
        datasets = _elements_of_type(spdx, "dataset_Dataset")
        assert datasets, "DATASTORE should produce dataset_Dataset"

    @pytest.mark.parametrize(
        "ctype",
        [
            ComponentType.FRAMEWORK,
            ComponentType.TOOL,
            ComponentType.GUARDRAIL,
            ComponentType.AUTH,
            ComponentType.PROMPT,
            ComponentType.DEPLOYMENT,
            ComponentType.API_ENDPOINT,
            ComponentType.PRIVILEGE,
            ComponentType.IAM,
        ],
    )
    def test_other_types_produce_software_Package(self, ctype: ComponentType) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ctype, ctype.value)])
        pkgs = [
            e
            for e in _graph(spdx)
            if e.get("type") == "software_Package" and e.get("xelo:componentType") == ctype.value
        ]
        assert pkgs, f"{ctype.value} should produce software_Package"


# ── Package properties (reference format) ─────────────────────────────────────


class TestPackageProperties:
    def test_packages_use_software_packageVersion(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        assert "software_packageVersion" in pkg, (
            "Must use software_packageVersion (not packageVersion)"
        )
        assert "packageVersion" not in pkg

    def test_packages_have_releaseTime(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        assert "releaseTime" in pkg

    def test_packages_have_suppliedBy(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        assert "suppliedBy" in pkg

    def test_suppliedBy_is_string_not_list(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        assert isinstance(pkg["suppliedBy"], str)

    def test_suppliedBy_references_org(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        org_ids = {e["spdxId"] for e in _elements_of_type(spdx, "Organization")}
        assert pkg["suppliedBy"] in org_ids, "suppliedBy must reference an Organization spdxId"


# ── Relationship elements ─────────────────────────────────────────────────────


class TestRelationships:
    def _make_edge(
        self, src: Node, tgt: Node, rtype: RelationshipType = RelationshipType.USES
    ) -> Edge:
        return Edge(source=src.id, target=tgt.id, relationship_type=rtype)

    def test_edges_produce_relationship_elements(self) -> None:
        src = _make_node(ComponentType.AGENT, "agent-1")
        tgt = _make_node(ComponentType.MODEL, "model-1")
        spdx = _minimal_doc(nodes=[src, tgt], edges=[self._make_edge(src, tgt)])
        rels = _elements_of_type(spdx, "Relationship")
        assert rels, "Edges should produce Relationship elements"

    def test_relationship_has_completeness_complete(self) -> None:
        src = _make_node(ComponentType.AGENT, "a")
        tgt = _make_node(ComponentType.MODEL, "m")
        spdx = _minimal_doc(nodes=[src, tgt], edges=[self._make_edge(src, tgt)])
        rels = _elements_of_type(spdx, "Relationship")
        assert all(r.get("completeness") == "complete" for r in rels)

    def test_relationship_has_from_and_to(self) -> None:
        src = _make_node(ComponentType.AGENT, "agent2")
        tgt = _make_node(ComponentType.MODEL, "model2")
        spdx = _minimal_doc(nodes=[src, tgt], edges=[self._make_edge(src, tgt)])
        rel = _elements_of_type(spdx, "Relationship")[0]
        assert "from" in rel
        assert isinstance(rel["to"], list) and rel["to"]

    def test_relationship_type_uses_maps_to_dependsOn(self) -> None:
        src = _make_node(ComponentType.AGENT, "a3")
        tgt = _make_node(ComponentType.MODEL, "m3")
        spdx = _minimal_doc(
            nodes=[src, tgt],
            edges=[self._make_edge(src, tgt, RelationshipType.USES)],
        )
        rel = _elements_of_type(spdx, "Relationship")[0]
        assert rel["relationshipType"] == "dependsOn"

    def test_relationship_type_deploys_maps_to_generates(self) -> None:
        src = _make_node(ComponentType.DEPLOYMENT, "d1")
        tgt = _make_node(ComponentType.MODEL, "m4")
        spdx = _minimal_doc(
            nodes=[src, tgt],
            edges=[self._make_edge(src, tgt, RelationshipType.DEPLOYS)],
        )
        rel = _elements_of_type(spdx, "Relationship")[0]
        assert rel["relationshipType"] == "generates"

    def test_relationships_in_sbom_element_list(self) -> None:
        src = _make_node(ComponentType.AGENT, "ag")
        tgt = _make_node(ComponentType.MODEL, "mo")
        spdx = _minimal_doc(nodes=[src, tgt], edges=[self._make_edge(src, tgt)])
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        rels = _elements_of_type(spdx, "Relationship")
        for rel in rels:
            assert rel["spdxId"] in sbom["element"]

    def test_no_edges_no_relationship_elements(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        assert not _elements_of_type(spdx, "Relationship")


# ── Extension properties ──────────────────────────────────────────────────────


class TestExtensionProperties:
    def test_all_nodes_have_xelo_confidence(self) -> None:
        spdx = _minimal_doc(
            nodes=[
                _make_node(ComponentType.MODEL),
                _make_node(ComponentType.AGENT),
            ]
        )
        ai_pkgs = _elements_of_type(spdx, "ai_AIPackage")
        for pkg in ai_pkgs:
            assert "xelo:confidence" in pkg
            assert pkg["xelo:confidence"] == pytest.approx(0.75)

    def test_model_primary_purpose(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        assert pkg.get("software_primaryPurpose") == "model"

    def test_model_type_of_model_from_metadata(self) -> None:
        meta = NodeMetadata(model_name="gpt-4o")
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.MODEL, metadata=meta)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        assert pkg.get("ai_typeOfModel") == ["gpt-4o"]

    def test_agent_autonomy_type(self) -> None:
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.AGENT)])
        pkg = _elements_of_type(spdx, "ai_AIPackage")[0]
        assert pkg.get("ai_autonomyType") == "yes"

    def test_datastore_dataset_type_from_metadata(self) -> None:
        meta = NodeMetadata(datastore_type="postgres")
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.DATASTORE, metadata=meta)])
        ds = _elements_of_type(spdx, "dataset_Dataset")[0]
        assert ds.get("dataset_datasetType") == "postgres"
        assert ds.get("xelo:datasetType") == "postgres"

    def test_datastore_data_classification(self) -> None:
        meta = NodeMetadata(data_classification=["PHI", "PII"])
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.DATASTORE, metadata=meta)])
        ds = _elements_of_type(spdx, "dataset_Dataset")[0]
        assert json.loads(ds["xelo:dataClassification"]) == ["PHI", "PII"]

    def test_auth_extension_properties(self) -> None:
        meta = NodeMetadata(auth_type="oauth2", auth_class="OAuth2Provider")
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.AUTH, metadata=meta)])
        pkg = next(e for e in _graph(spdx) if e.get("xelo:componentType") == "AUTH")
        assert pkg["xelo:authType"] == "oauth2"
        assert pkg["xelo:authClass"] == "OAuth2Provider"

    def test_iam_extension_properties(self) -> None:
        meta = NodeMetadata(iam_type="role", principal="arn:aws:iam::123:role/MyRole")
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.IAM, metadata=meta)])
        pkg = next(e for e in _graph(spdx) if e.get("xelo:componentType") == "IAM")
        assert pkg["xelo:iamType"] == "role"
        assert pkg["xelo:principal"] == "arn:aws:iam::123:role/MyRole"

    def test_deployment_extension(self) -> None:
        meta = NodeMetadata(deployment_target="kubernetes")
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.DEPLOYMENT, metadata=meta)])
        pkg = next(e for e in _graph(spdx) if e.get("xelo:componentType") == "DEPLOYMENT")
        assert pkg["xelo:deploymentTarget"] == "kubernetes"

    def test_api_endpoint_extension(self) -> None:
        meta = NodeMetadata(endpoint="/api/v1/chat", transport="http", method="POST")
        spdx = _minimal_doc(nodes=[_make_node(ComponentType.API_ENDPOINT, metadata=meta)])
        pkg = next(e for e in _graph(spdx) if e.get("xelo:componentType") == "API_ENDPOINT")
        assert pkg["xelo:endpoint"] == "/api/v1/chat"
        assert pkg["xelo:transport"] == "http"
        assert pkg["xelo:method"] == "POST"


# ── PackageDep serialisation ──────────────────────────────────────────────────


class TestPackageDep:
    def _make_dep(self, name: str, purl: str, version: str = ">=1.0") -> PackageDep:
        return PackageDep(
            name=name,
            purl=purl,
            version_spec=version,
            group="default",
            source_file="requirements.txt",
        )

    def test_dep_produces_software_Package(self) -> None:
        dep = self._make_dep("numpy", "pkg:pypi/numpy@1.24.0")
        spdx = _minimal_doc(deps=[dep])
        pkgs = [
            e
            for e in _graph(spdx)
            if e.get("type") == "software_Package" and e.get("name") == "numpy"
        ]
        assert pkgs

    def test_dep_has_purl_in_external_identifier(self) -> None:
        dep = self._make_dep("requests", "pkg:pypi/requests@2.31.0")
        spdx = _minimal_doc(deps=[dep])
        pkg = next(e for e in _graph(spdx) if e.get("name") == "requests")
        ext_ids = pkg.get("externalIdentifier", [])
        assert any(eid.get("identifier") == "pkg:pypi/requests@2.31.0" for eid in ext_ids)

    def test_dep_external_identifier_type_is_purl(self) -> None:
        dep = self._make_dep("torch", "pkg:pypi/torch@2.0.0")
        spdx = _minimal_doc(deps=[dep])
        pkg = next(e for e in _graph(spdx) if e.get("name") == "torch")
        ext_ids = pkg.get("externalIdentifier", [])
        assert any(eid.get("externalIdentifierType") == "purl" for eid in ext_ids)

    def test_dep_uses_software_packageVersion(self) -> None:
        dep = self._make_dep("fastapi", "pkg:pypi/fastapi@0.100.0", ">=0.100.0")
        spdx = _minimal_doc(deps=[dep])
        pkg = next(e for e in _graph(spdx) if e.get("name") == "fastapi")
        assert pkg.get("software_packageVersion") == ">=0.100.0"
        assert "packageVersion" not in pkg

    def test_dep_has_releaseTime(self) -> None:
        dep = self._make_dep("pydantic", "pkg:pypi/pydantic@2.0.0")
        spdx = _minimal_doc(deps=[dep])
        pkg = next(e for e in _graph(spdx) if e.get("name") == "pydantic")
        assert "releaseTime" in pkg

    def test_dep_has_suppliedBy(self) -> None:
        dep = self._make_dep("httpx", "pkg:pypi/httpx@0.24.0")
        spdx = _minimal_doc(deps=[dep])
        pkg = next(e for e in _graph(spdx) if e.get("name") == "httpx")
        assert "suppliedBy" in pkg


# ── ScanSummary ───────────────────────────────────────────────────────────────


class TestScanSummary:
    def test_summary_stored_on_sbom_element(self) -> None:
        summary = ScanSummary(frameworks=["langgraph"], modalities=["TEXT"])
        spdx = _minimal_doc(summary=summary)
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert sbom.get("xelo:frameworks") == ["langgraph"]
        assert sbom.get("xelo:modalities") == ["TEXT"]

    def test_summary_security_findings(self) -> None:
        summary = ScanSummary(security_findings=["container_runs_as_root"])
        spdx = _minimal_doc(summary=summary)
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert sbom.get("xelo:securityFindings") == ["container_runs_as_root"]

    def test_no_summary_no_extra_fields(self) -> None:
        spdx = _minimal_doc()
        sbom = _elements_of_type(spdx, "software_Sbom")[0]
        assert "xelo:frameworks" not in sbom
        assert "xelo:modalities" not in sbom


# ── Plugin .run() path ────────────────────────────────────────────────────────


class TestPluginRun:
    def _run(self, **kwargs: Any) -> dict[str, Any]:
        doc = AiSbomDocument(
            target="test",
            nodes=kwargs.get("nodes", []),
            deps=kwargs.get("deps", []),
            generated_at=_FIXED_TS,
        )
        plugin = SpdxExporter()
        result = plugin.run(doc.model_dump(mode="json"), {})
        return result.details  # type: ignore[return-value]

    def test_plugin_status_ok(self) -> None:
        doc = AiSbomDocument(target="t", generated_at=_FIXED_TS)
        plugin = SpdxExporter()
        result = plugin.run(doc.model_dump(mode="json"), {})
        assert result.status == "ok"

    def test_plugin_result_has_graph(self) -> None:
        spdx = self._run()
        assert "@graph" in spdx

    def test_plugin_result_has_spdx_document(self) -> None:
        spdx = self._run()
        docs = [e for e in spdx["@graph"] if e.get("type") == "SpdxDocument"]
        assert docs

    def test_plugin_result_nodes_present(self) -> None:
        nodes = [_make_node(ComponentType.MODEL)]
        spdx = self._run(nodes=nodes)
        pkgs = [e for e in spdx["@graph"] if e.get("type") == "ai_AIPackage"]
        assert pkgs
