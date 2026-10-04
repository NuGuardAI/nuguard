"""End-to-end deployment topology: workloads ↔ images ↔ code ↔ gateways, via the full pipeline."""
# Tests assert each optional sub-model is present before reading it; the asserts are the guard.
# mypy: disable-error-code="union-attr"

from __future__ import annotations

from pathlib import Path

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.enricher import enrich
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType

_API_PY = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/items")
async def list_items():
    return []
'''


def _run(tmp_path: Path, files: dict[str, str], *, twice: bool = False) -> AiSbomDocument:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig())
    enrich(doc)
    if twice:
        enrich(doc)
    return doc


def _key(name: str) -> str:
    """Node names are prettified ('web-app' -> 'Web App'); compare ignoring case and dashes."""
    return name.lower().replace("-", " ")


def _node(doc: AiSbomDocument, name: str, ctype: ComponentType = ComponentType.DEPLOYMENT) -> Node:
    return next(n for n in doc.nodes if _key(n.name) == _key(name) and n.component_type == ctype)


def _edges(doc: AiSbomDocument, rel: str) -> set[tuple[str, str]]:
    names = {n.id: n.name for n in doc.nodes}
    return {
        (_key(names[e.source]), _key(names[e.target]))
        for e in doc.edges
        if e.relationship_type.value == rel
    }


_COMPOSE = """
services:
  gateway:
    build: ./gateway
    ports: ["8080:80"]
    depends_on: [api]
  api:
    build: { context: ./src/api }
    ports: ["9000:8000"]
    environment:
      - DB_HOST=db
  db:
    image: postgres:16
"""
_NGINX = """
events {}
http {
  server {
    listen 80;
    location / { proxy_pass http://api:8000; }
  }
}
"""


def test_compose_stack_links_images_code_gateway_and_exposure(tmp_path: Path) -> None:
    doc = _run(
        tmp_path,
        {
            "docker-compose.yml": _COMPOSE,
            "gateway/Dockerfile": "FROM nginx:1.25-alpine\nEXPOSE 80\n",
            "gateway/nginx.conf": _NGINX,
            "src/api/Dockerfile": "FROM python:3.11-slim\nEXPOSE 8000\n",
            "src/api/main.py": _API_PY,
        },
        twice=True,
    )
    api = _node(doc, "api")
    assert api.metadata.workload.source_dir == "src/api"
    # image layer
    assert ("api", "api image") in _edges(doc, "RUNS")
    assert ("db", "postgres:16") in _edges(doc, "RUNS")
    assert ("api image", "python:3.11 slim") in _edges(doc, "BUILT_FROM")
    # code hosted by the workload, with exposure derived from the published port
    endpoint = next(n for n in doc.nodes if n.component_type == ComponentType.API_ENDPOINT)
    assert ("api", _key(endpoint.name)) in _edges(doc, "HOSTS")
    assert endpoint.metadata.hosted_by == "api"
    assert endpoint.metadata.network_exposure == "public"
    assert ("api", _key(endpoint.name)) in _edges(doc, "EXPOSES")
    # relationships between services
    assert ("gateway", "api") in _edges(doc, "DEPENDS_ON")
    assert any(dst == "api" for _src, dst in _edges(doc, "ROUTES_TO"))
    # no leftover fact markers and no edge duplication after a second enrich pass
    assert not any(n.metadata.extras.get("fact_marker") for n in doc.nodes)
    keys = [(e.source, e.target, e.relationship_type) for e in doc.edges]
    assert len(keys) == len(set(keys))


def test_internal_only_workload_marks_endpoint_not_public(tmp_path: Path) -> None:
    doc = _run(
        tmp_path,
        {
            "docker-compose.yml": "services:\n  api:\n    build: ./api\n    expose: ['8000']\n",
            "api/Dockerfile": "FROM python:3.11-slim\n",
            "api/main.py": _API_PY,
        },
    )
    endpoint = next(n for n in doc.nodes if n.component_type == ComponentType.API_ENDPOINT)
    assert endpoint.metadata.network_exposure == "cluster"
    assert not _edges(doc, "EXPOSES")
    assert _node(doc, "api").metadata.workload.internal_only is True


_K8S = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: api, namespace: shop}
spec:
  replicas: 3
  template:
    metadata: {labels: {app: api}}
    spec:
      containers:
        - {name: api, image: "ghcr.io/acme/api:1.4", ports: [{containerPort: 8080}]}
---
apiVersion: v1
kind: Service
metadata: {name: api-svc, namespace: shop}
spec:
  selector: {app: api}
  ports: [{port: 80, targetPort: 8080}]
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: {name: api-hpa, namespace: shop}
spec:
  scaleTargetRef: {kind: Deployment, name: api}
  minReplicas: 2
  maxReplicas: 10
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: web, namespace: shop}
spec:
  rules:
    - host: shop.example.com
      http:
        paths:
          - {path: /api, backend: {service: {name: api-svc, port: {number: 80}}}}
"""
_KUSTOMIZE = """
namespace: shop
images: [{name: ghcr.io/acme/api, newTag: "2.0"}]
replicas: [{name: api, count: 5}]
resources: [../base]
"""


def test_k8s_service_hpa_ingress_and_kustomize_are_joined(tmp_path: Path) -> None:
    doc = _run(
        tmp_path,
        {
            "k8s/base/app.yaml": _K8S,
            "k8s/overlays/prod/kustomization.yaml": _KUSTOMIZE,
            "api/Dockerfile": "FROM python:3.11-slim\n",
            "api/main.py": _API_PY,
        },
        twice=True,
    )
    wl = _node(doc, "api").metadata.workload
    port = next(p for p in wl.ports if p.container_port == 8080)
    assert port.service_port == 80
    assert port.exposure == "public"  # fronted by the Ingress
    assert wl.internal_only is False
    assert (wl.scaling.min_replicas, wl.scaling.max_replicas, wl.scaling.autoscaler) == (2, 10, "hpa")
    assert wl.scaling.replicas == 5  # Kustomize replicas override
    assert wl.image_refs == ["ghcr.io/acme/api:2.0"]  # Kustomize image tag override
    assert any(dst == "api" for src, dst in _edges(doc, "ROUTES_TO") if src.startswith("ingress"))
    # Service / HPA / Kustomize fact markers merged and dropped
    assert not any(n.metadata.extras.get("fact_marker") for n in doc.nodes)
    # no Dockerfile reference in the manifest: linked to code by image name → directory (heuristic)
    assert wl.source_dir == "api"
    host_edges = [e for e in doc.edges if e.relationship_type.value == "HOSTS"]
    assert host_edges and all(e.derivation == "fallback_heuristic" for e in host_edges)


_BICEP = """
param location string = 'eastus'
resource web 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'web-app'
  tags: { 'azd-service-name': 'web' }
  properties: {
    configuration: { ingress: { external: false, targetPort: 8000 } }
    template: {
      containers: [ { name: 'web', image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' } ]
      scale: { minReplicas: 1, maxReplicas: 4 }
    }
  }
}
"""
_AZD = "name: app\nservices:\n  web:\n    project: ./src/web\n    host: containerapp\n"


def test_azd_project_links_bicep_container_app_to_code(tmp_path: Path) -> None:
    doc = _run(
        tmp_path,
        {"azure.yaml": _AZD, "infra/main.bicep": _BICEP, "src/web/app.py": _API_PY},
    )
    web = _node(doc, "web-app")
    assert web.metadata.workload.source_dir == "src/web"
    assert web.metadata.workload.scaling.max_replicas == 4
    endpoint = next(n for n in doc.nodes if n.component_type == ComponentType.API_ENDPOINT)
    hosts = [e for e in doc.edges if e.relationship_type.value == "HOSTS" and e.source == web.id]
    assert hosts and all(e.derivation == "hint" for e in hosts)  # declared by azure.yaml
    assert endpoint.metadata.hosted_by == "web-app"
    assert endpoint.metadata.network_exposure == "internal"  # internal ACA ingress
    assert not _edges(doc, "EXPOSES")
    assert not any(n.metadata.extras.get("fact_marker") for n in doc.nodes)  # azd marker dropped


def test_large_compose_file_is_not_collapsed_as_bulk_catalog(tmp_path: Path) -> None:
    services = "\n".join(
        f"  svc{i}:\n    image: busybox:1.{i}\n    ports: ['{9000 + i}:80']" for i in range(30)
    )
    doc = _run(tmp_path, {"docker-compose.yml": f"services:\n{services}\n"})
    workloads = [n for n in doc.nodes if n.metadata.workload]
    assert len(workloads) == 30
    assert not any(n.metadata.extras.get("bulk_catalog_truncated") for n in doc.nodes)
    # each workload runs exactly its own image; no proximity-guessed DEPLOYS fan-out
    assert len(_edges(doc, "RUNS")) == 30
    assert not _edges(doc, "DEPLOYS")


def test_topology_pass_is_a_noop_without_deployment_facts(tmp_path: Path) -> None:
    doc = _run(tmp_path, {"app/main.py": _API_PY})
    assert not any(e.relationship_type.value in {"HOSTS", "RUNS", "EXPOSES"} for e in doc.edges)
    endpoint = next(n for n in doc.nodes if n.component_type == ComponentType.API_ENDPOINT)
    assert endpoint.metadata.hosted_by is None and endpoint.metadata.network_exposure is None
