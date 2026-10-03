"""Container image layer: ref parsing, OS inference, Dockerfile stages/packages, syft scan."""
# mypy: disable-error-code="union-attr,index"

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Literal

import pytest

from nuguard.sbom.adapters.dockerfile import DockerfileAdapter
from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.image_os import infer_os
from nuguard.sbom.image_ref import (
    image_basename,
    is_templated,
    normalize_image_ref,
    parse_image_ref,
)
from nuguard.sbom.image_scan import parse_syft_json, scan_images
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType


# --------------------------------------------------------------------------- refs
def test_parse_image_ref_components() -> None:
    assert parse_image_ref("gcr.io/proj/app:1.2") == {
        "registry": "gcr.io", "name": "proj/app", "tag": "1.2", "digest": None,
    }
    assert parse_image_ref("localhost:5000/img:t")["registry"] == "localhost:5000"
    assert parse_image_ref("ubuntu@sha256:abcdef1234")["digest"] == "sha256:abcdef1234"


def test_normalize_and_basename_match_implicit_defaults() -> None:
    assert normalize_image_ref("docker.io/library/Redis") == normalize_image_ref("redis:latest")
    assert image_basename("acr.azurecr.io/org/web:1") == "web"


@pytest.mark.parametrize("ref", ["${BASE}", "{{ .Values.image }}", "$IMG", "[parameters('x')]"])
def test_is_templated(ref: str) -> None:
    assert is_templated(ref)


# --------------------------------------------------------------------------- OS inference
@pytest.mark.parametrize(
    ("ref", "name", "version", "family", "evidence"),
    [
        ("ubuntu:22.04", "ubuntu", "22.04", "debian", "tag"),
        ("ubuntu:jammy", "ubuntu", "22.04", "debian", "tag"),
        ("debian:bookworm-slim", "debian", "12", "debian", "tag"),
        ("alpine:3.19", "alpine", "3.19", "alpine", "tag"),
        ("python:3.11-slim-bookworm", "debian", "12", "debian", "tag"),
        ("node:20-alpine3.19", "alpine", "3.19", "alpine", "tag"),
        ("python:3.11-slim", "debian", None, "debian", "image_default"),
        ("nginx:1.25", "debian", None, "debian", "image_default"),
        ("nginx:1.25-alpine", "alpine", None, "alpine", "tag"),
        ("redis:7.4-alpine", "alpine", None, "alpine", "tag"),
        ("gcr.io/distroless/python3-debian12", "distroless", "12", "distroless", "tag"),
        ("registry.access.redhat.com/ubi9/ubi:9.3", "rhel", "9", "rhel", "tag"),
        ("amazonlinux:2023", "amazonlinux", "2023", "rhel", "tag"),
        ("mcr.microsoft.com/dotnet/aspnet:8.0", "debian", None, "debian", "image_default"),
        ("mcr.microsoft.com/dotnet/aspnet:8.0-alpine", "alpine", None, "alpine", "tag"),
    ],
)
def test_infer_os(ref: str, name: str, version: str | None, family: str, evidence: str) -> None:
    info = infer_os(ref)
    assert info is not None
    assert (info.name, info.version, info.family, info.evidence) == (name, version, family, evidence)


def test_infer_os_unknown_and_scratch() -> None:
    assert infer_os("scratch") is None
    assert infer_os("registry.example.com/team/custom:1") is None


# --------------------------------------------------------------------------- Dockerfile
_DF = """\
ARG BASE=python:3.11-slim-bookworm
FROM ${BASE} AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev=15.1 \\
    curl
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt fastapi==0.110 "uvicorn[standard]" ./local
FROM builder AS runtime
WORKDIR /app
ENV API_KEY=abc
USER app
EXPOSE 8000 9090/udp
HEALTHCHECK CMD curl -f http://localhost:8000/health
ENTRYPOINT ["uvicorn", "main:app", "--password=hunter2"]
"""


def _scan(content: str = _DF, path: str = "src/api/Dockerfile", **kw: Any) -> dict[str, Any]:
    dets = DockerfileAdapter(**kw).scan(content, path)
    return {d.canonical_name: d for d in dets}


def test_dockerfile_emits_one_base_node_and_one_app_node() -> None:
    dets = _scan()
    base = dets["container_image:python:3.11-slim-bookworm"]
    app = dets["container_image:app:src/api/dockerfile"]
    assert base.metadata["image_role"] == "base"
    assert (base.metadata["os_name"], base.metadata["os_version"]) == ("debian", "12")
    assert app.metadata["image_role"] == "app"
    assert app.metadata["base_image"] == "python:3.11-slim-bookworm"
    # FROM <earlier stage alias> is not an image of its own
    assert not any("builder" in k for k in dets)
    assert [(r.relationship_type, r.target_canonical) for r in app.relationships] == [
        ("BUILT_FROM", "container_image:python:3.11-slim-bookworm")
    ]


def test_dockerfile_app_node_final_stage_facts() -> None:
    meta = _scan()["container_image:app:src/api/dockerfile"].metadata
    assert meta["stage_alias"] == "runtime"
    assert meta["workdir"] == "/app"
    assert meta["runs_as_root"] is False
    assert meta["has_health_check"] is True
    assert meta["multi_stage_build"] is True
    assert meta["dependency_manifests"] == ["requirements.txt"]
    assert meta["exposed_ports"] == [
        {"container_port": 8000, "protocol": "tcp", "exposure": "unknown", "source": "EXPOSE"},
        {"container_port": 9090, "protocol": "udp", "exposure": "unknown", "source": "EXPOSE"},
    ]


def test_dockerfile_redacts_secret_values_in_entrypoint() -> None:
    meta = _scan()["container_image:app:src/api/dockerfile"].metadata
    assert "hunter2" not in meta["entrypoint"]
    assert "--password=***" in meta["entrypoint"]


def test_dockerfile_run_packages_by_manager() -> None:
    pkgs = _scan()["container_image:app:src/api/dockerfile"].metadata["image_packages"]
    got = {(p["manager"], p["name"], p["version"]) for p in pkgs}
    assert ("apt", "gcc", None) in got
    assert ("apt", "libpq-dev", "15.1") in got
    assert ("apt", "curl", None) in got  # continuation line joined
    assert ("pip", "fastapi", "0.110") in got
    assert ("pip", "uvicorn", None) in got  # extras stripped
    # flags, requirement files and local paths are not packages
    assert not any(p["name"] in {"requirements.txt", "./local", "-r", "--no-cache-dir"} for p in pkgs)
    assert all(p["source"] == "dockerfile_run" for p in pkgs)


def test_dockerfile_package_cap() -> None:
    df = "FROM alpine:3.19\nRUN apk add " + " ".join(f"pkg{i}" for i in range(50)) + "\n"
    pkgs = _scan(df, "Dockerfile", max_packages=10)["container_image:app:dockerfile"].metadata[
        "image_packages"
    ]
    assert len(pkgs) == 10


def test_dockerfile_unresolved_arg_and_scratch_skipped() -> None:
    dets = _scan("ARG X\nFROM ${X}\nFROM scratch\nCOPY a /a\n", "Dockerfile")
    assert not any(d.metadata.get("image_role") == "base" for d in dets.values())


def test_dockerfile_root_user_and_no_healthcheck() -> None:
    meta = _scan("FROM python:3.12\nUSER root\n", "Dockerfile")[
        "container_image:app:dockerfile"
    ].metadata
    assert meta["runs_as_root"] is True
    assert "has_health_check" not in meta


def test_dockerfile_variant_names_are_scanned_and_dockerignore_detected(tmp_path: Path) -> None:
    svc = tmp_path / "svc"
    svc.mkdir()
    (svc / "Dockerfile.dev").write_text("FROM node:20-alpine\nEXPOSE 3000\n")
    (svc / ".dockerignore").write_text(".env\n.git\n")
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig())
    app = next(n for n in doc.nodes if n.metadata.image_role == "app")
    assert app.metadata.has_dockerignore is True
    assert (app.metadata.os_name, app.metadata.os_family) == ("alpine", "alpine")
    assert app.metadata.exposed_ports[0].container_port == 3000
    base = next(n for n in doc.nodes if n.metadata.image_role == "base")
    edge_types = {(e.source, e.target, e.relationship_type.value) for e in doc.edges}
    assert (app.id, base.id, "BUILT_FROM") in edge_types


# --------------------------------------------------------------------------- syft
_SYFT_JSON = json.dumps(
    {
        "distro": {"id": "debian", "versionID": "12", "idLike": ""},
        "artifacts": [
            {"name": "openssl", "version": "3.0.11", "type": "deb"},
            {"name": "openssl", "version": "3.0.11", "type": "deb"},  # duplicate
            {"name": "requests", "version": "2.31.0", "type": "python"},
        ],
    }
)


def _doc_with_images() -> AiSbomDocument:
    doc = AiSbomDocument(target="t", nodes=[], edges=[])
    roles: list[tuple[str, Literal["base", "app"]]] = [
        ("python:3.11-slim", "base"), ("${X}", "base"), ("app:1", "app"),
    ]
    for ref, role in roles:
        doc.nodes.append(
            Node(
                name=ref,
                component_type=ComponentType.CONTAINER_IMAGE,
                confidence=0.9,
                metadata=NodeMetadata(base_image=ref, image_role=role),
            )
        )
    return doc


def test_parse_syft_json_dedupes_and_caps() -> None:
    distro, pkgs = parse_syft_json(_SYFT_JSON, max_packages=10)
    assert distro["id"] == "debian"
    assert [(p.name, p.manager) for p in pkgs] == [("openssl", "syft:deb"), ("requests", "syft:python")]
    assert len(parse_syft_json(_SYFT_JSON, max_packages=1)[1]) == 1


def test_scan_images_enriches_only_pulled_base_images() -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_SYFT_JSON, stderr="")

    doc = _doc_with_images()
    assert scan_images(doc, runner=runner, which=lambda _: "/usr/bin/syft") == 1
    assert [c[1] for c in calls] == ["python:3.11-slim"]  # not the templated ref, not the app image
    meta = doc.nodes[0].metadata
    assert (meta.os_name, meta.os_version, meta.os_evidence) == ("debian", "12", "syft")
    assert [p.name for p in meta.image_packages] == ["openssl", "requests"]
    assert doc.nodes[2].metadata.image_packages is None


def test_scan_images_degrades_without_syft_or_on_failure() -> None:
    doc = _doc_with_images()
    assert scan_images(doc, which=lambda _: None) == 0

    def boom(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, 1)

    assert scan_images(doc, runner=boom, which=lambda _: "/x/syft") == 0
    assert doc.nodes[0].metadata.os_name is None

    def failing(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pull failed")

    assert scan_images(doc, runner=failing, which=lambda _: "/x/syft") == 0


def test_scan_images_is_off_by_default() -> None:
    assert AiSbomConfig().scan_images is False
    assert AiSbomConfig().max_image_packages == 200
