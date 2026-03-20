"""Test SPDX 3.0.1 export from AiSbomSerializer."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nuguard.models.sbom import AiSbomDocument, PackageDep, ScanSummary
from nuguard.sbom.extractor.serializer import AiSbomSerializer


def _doc_with_deps(*deps: PackageDep) -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(timezone.utc),
        target="my-ai-app",
        deps=list(deps),
        summary=ScanSummary(),
    )


def test_spdx_context_field() -> None:
    """SPDX output has @context field."""
    doc = _doc_with_deps()
    spdx = AiSbomSerializer.to_spdx(doc)
    assert "@context" in spdx
    assert "spdx" in spdx["@context"].lower()


def test_spdx_graph_field() -> None:
    """SPDX output has @graph array."""
    doc = _doc_with_deps()
    spdx = AiSbomSerializer.to_spdx(doc)
    assert "@graph" in spdx
    assert len(spdx["@graph"]) >= 1


def test_spdx_document_type() -> None:
    """Root element is of type SpdxDocument."""
    doc = _doc_with_deps()
    spdx = AiSbomSerializer.to_spdx(doc)
    root = spdx["@graph"][0]
    assert root["type"] == "SpdxDocument"


def test_spdx_document_name() -> None:
    """SpdxDocument name matches doc.target."""
    doc = _doc_with_deps()
    spdx = AiSbomSerializer.to_spdx(doc)
    root = spdx["@graph"][0]
    assert root["name"] == "my-ai-app"


def test_spdx_packages_populated() -> None:
    """PackageDeps are mapped to SPDX packages."""
    deps = [
        PackageDep(name="requests", version_spec=">=2.28.0", ecosystem="pypi"),
        PackageDep(name="pydantic", version_spec=">=2.0.0", ecosystem="pypi"),
    ]
    doc = _doc_with_deps(*deps)
    spdx = AiSbomSerializer.to_spdx(doc)
    packages = spdx["@graph"][0]["packages"]
    assert len(packages) == 2
    names = {p["name"] for p in packages}
    assert "requests" in names
    assert "pydantic" in names


def test_spdx_package_spdx_id() -> None:
    """Each package has a spdxId field."""
    dep = PackageDep(name="httpx", version_spec=">=0.27", ecosystem="pypi")
    doc = _doc_with_deps(dep)
    spdx = AiSbomSerializer.to_spdx(doc)
    pkg = spdx["@graph"][0]["packages"][0]
    assert "spdxId" in pkg
    assert "SPDXRef" in pkg["spdxId"]


def test_spdx_package_version() -> None:
    """Package version is stripped of operators."""
    dep = PackageDep(name="requests", version_spec=">=2.28.0", ecosystem="pypi")
    doc = _doc_with_deps(dep)
    spdx = AiSbomSerializer.to_spdx(doc)
    pkg = spdx["@graph"][0]["packages"][0]
    assert "versionInfo" in pkg
    assert pkg["versionInfo"] == "2.28.0"


def test_spdx_package_external_refs() -> None:
    """Each package has PURL in externalRefs."""
    dep = PackageDep(name="fastapi", version_spec=">=0.100", ecosystem="pypi")
    doc = _doc_with_deps(dep)
    spdx = AiSbomSerializer.to_spdx(doc)
    pkg = spdx["@graph"][0]["packages"][0]
    assert "externalRefs" in pkg
    assert len(pkg["externalRefs"]) >= 1
    purl = pkg["externalRefs"][0]["locator"]
    assert "fastapi" in purl
    assert "pkg:" in purl


def test_spdx_empty_deps() -> None:
    """SPDX document with no deps has empty packages list."""
    doc = _doc_with_deps()
    spdx = AiSbomSerializer.to_spdx(doc)
    packages = spdx["@graph"][0]["packages"]
    assert packages == []


def test_spdx_creation_info() -> None:
    """SpdxDocument has creationInfo with tool name."""
    doc = _doc_with_deps()
    spdx = AiSbomSerializer.to_spdx(doc)
    root = spdx["@graph"][0]
    assert "creationInfo" in root
    creators = root["creationInfo"]["createdBy"]
    assert any(c.get("name") == "nuguard" for c in creators)
