"""Public contract for the SBOM 1.7.0 container / deployment layer.

Covers: schema version, additive public models and edge types, config defaults on the public
request, JSON round-trips, bundled-schema validity of a generated SBOM, and that environment
variable / secret *values* never reach serialized output.
"""
# mypy: disable-error-code="union-attr,index"

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nuguard.sbom.models import (
    AiSbomDocument,
    ImagePackage,
    PortDetail,
    ResourceDetail,
    ScalingDetail,
    WorkloadDetail,
)
from nuguard.sbom.public_api import (
    SbomGenerateRequest,
    SbomRenderRequest,
    generate_sbom,
    render_sbom,
)
from nuguard.sbom.types import RelationshipType

_NEW_MODELS = ("WorkloadDetail", "PortDetail", "ScalingDetail", "ResourceDetail", "ImagePackage")
_NEW_EDGES = {"RUNS", "BUILT_FROM", "HOSTS", "EXPOSES", "ROUTES_TO", "DEPENDS_ON"}
_SECRET_VALUE = "s3cr3t-do-not-leak-9f8e7d"


def _public_schema() -> dict[str, Any]:
    path = Path(__file__).with_name("public_api.schema.json")
    return json.loads(path.read_text())


def test_schema_version_is_1_7_0() -> None:
    assert AiSbomDocument.model_json_schema()["properties"]["schema_version"]["default"] == "1.7.0"
    assert AiSbomDocument.model_json_schema()["$id"].endswith("/aibom/1.7.0/aibom.schema.json")


def test_public_snapshot_exposes_new_models_edges_and_config() -> None:
    schema = _public_schema()["sbom.SbomGenerateResult"]
    defs = schema["$defs"]
    for name in _NEW_MODELS:
        assert name in defs, f"{name} missing from public API schema"
    assert _NEW_EDGES <= set(defs["RelationshipType"]["enum"])
    node_props = defs["NodeMetadata"]["properties"]
    for field in ("workload", "cloud_provider", "image_role", "os_name", "os_version", "os_family",
                  "os_evidence", "image_packages", "exposed_ports", "hosted_by", "network_exposure"):
        assert field in node_props, field
    request = _public_schema()["sbom.SbomGenerateRequest"]["$defs"]["AiSbomConfig"]["properties"]
    assert request["scan_images"]["default"] is False
    assert request["max_image_packages"]["default"] == 200
    assert request["image_scan_timeout"]["default"] == 120


def test_new_edge_types_are_additive() -> None:
    legacy = {"USES", "CALLS", "ACCESSES", "PROTECTS", "DEPLOYS", "DELEGATES_TO", "CONTAINS"}
    assert legacy | _NEW_EDGES == {r.value for r in RelationshipType}


def test_new_models_round_trip_as_json() -> None:
    workload = WorkloadDetail(
        service_name="api",
        workload_kind="compose_service",
        ports=[PortDetail(container_port=8080, host_port=80, exposure="public", source="compose ports")],
        scaling=ScalingDetail(min_replicas=1, max_replicas=5, autoscaler="hpa"),
        resources=ResourceDetail(cpu_limit="500m", memory_limit="256Mi"),
        image_refs=["redis:7"],
        env_var_names=["DB_URL"],
    )
    again = WorkloadDetail.model_validate(json.loads(json.dumps(workload.model_dump(mode="json"))))
    assert again == workload
    pkg = ImagePackage(name="openssl", version="3.0", manager="apt", source="dockerfile_run")
    assert ImagePackage.model_validate(pkg.model_dump(mode="json")) == pkg


def test_old_documents_without_new_fields_still_validate() -> None:
    doc = AiSbomDocument.model_validate(
        {
            "target": "t",
            "schema_version": "1.6.0",
            "nodes": [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "name": "web",
                    "component_type": "DEPLOYMENT",
                    "confidence": 0.9,
                    "metadata": {"deployment_target": "kubernetes", "ha_mode": "replicated"},
                }
            ],
            "edges": [],
        }
    )
    assert doc.nodes[0].metadata.workload is None
    assert doc.nodes[0].metadata.network_exposure is None


@pytest.mark.asyncio
async def test_generate_sbom_emits_workload_facts_valid_against_bundled_schema_without_secret_values(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  api:\n"
        "    build: ./api\n"
        "    ports: ['8080:8000']\n"
        "    environment:\n"
        f"      - API_TOKEN={_SECRET_VALUE}\n"
        "      - LOG_LEVEL=debug\n"
        "  redis:\n"
        "    image: redis:7-alpine\n"
    )
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "Dockerfile").write_text(
        f"FROM python:3.11-slim-bookworm\nENV X=1\nENTRYPOINT [\"run\", \"--password={_SECRET_VALUE}\"]\n"
        "RUN pip install fastapi==0.110\n"
    )
    result = await generate_sbom(SbomGenerateRequest(source_path=str(tmp_path)))
    sbom = result.sbom
    workloads = {n.name.lower(): n for n in sbom.nodes if n.metadata.workload}
    assert set(workloads) == {"api", "redis"}
    assert workloads["api"].metadata.workload.env_var_names == ["API_TOKEN", "LOG_LEVEL"]
    app_image = next(n for n in sbom.nodes if n.metadata.image_role == "app")
    assert (app_image.metadata.os_name, app_image.metadata.os_version) == ("debian", "12")
    assert [p.name for p in app_image.metadata.image_packages] == ["fastapi"]

    dumped = sbom.model_dump_json()
    assert _SECRET_VALUE not in dumped
    rendered = await render_sbom(SbomRenderRequest(format="json"), sbom=sbom)
    assert _SECRET_VALUE not in json.dumps(rendered.model_dump(mode="json"), default=str)

    # the serialized document validates against the bundled schema and round-trips
    from nuguard.sbom.validator import validate_sbom  # noqa: PLC0415

    validate_sbom(json.loads(dumped))
    assert AiSbomDocument.model_validate_json(dumped).schema_version == "1.7.0"
