"""Ingress facts on DEPLOYMENT nodes (pentest-proposal A4)."""
# Tests assert each optional sub-model is present before reading it; the asserts are the guard.
# mypy: disable-error-code="union-attr"

from __future__ import annotations

from pathlib import Path

from nuguard.sbom.adapters.iac import BicepAdapter, K8sAdapter, TerraformAdapter
from nuguard.sbom.adapters.nginx import NginxAdapter
from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.ingress import collect_ingresses, has_split_ingress
from nuguard.sbom.models import AiSbomDocument, IngressDetail, IngressRole, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NGINX = """
server {
    listen 80;
    server_name bank.example.com;
    location /api/ {
        proxy_pass http://backend:8000;
    }
}
"""

_BICEP = """
param location string = 'eastus'
resource backend 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'backend'
  properties: {
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
      }
    }
  }
}
resource web 'Microsoft.Web/staticSites@2022-09-01' = {
  name: 'web'
}
"""

_BICEP_INTERNAL_ONLY = """
param location string = 'eastus'
resource backend 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'backend'
  properties: { configuration: { ingress: { external: false, targetPort: 8000 } } }
}
"""

_TERRAFORM = """
provider "azurerm" {}
resource "azurerm_container_app" "backend" {
  name = "backend"
  ingress {
    external_enabled = true
    target_port      = 8000
  }
}
resource "azurerm_static_web_app" "web" {
  name = "web"
}
resource "azurerm_api_management" "gw" {
  name = "gw"
}
"""

_K8S = """
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: bank
spec:
  rules:
    - host: bank.example.com
---
apiVersion: v1
kind: Service
metadata:
  name: backend-lb
spec:
  type: LoadBalancer
---
apiVersion: v1
kind: Service
metadata:
  name: backend-internal
spec:
  type: ClusterIP
"""


def _ingresses(detections) -> list[tuple[str, str | None, str]]:
    return sorted(
        (i["role"], i["url"], i["evidence"])
        for d in detections
        for i in d.metadata.get("ingresses", [])
    )


def test_nginx_proxy_is_a_gateway_with_its_public_host() -> None:
    detections = NginxAdapter().scan(_NGINX, "nginx.conf")
    assert _ingresses(detections) == [("gateway", "bank.example.com", "nginx proxy_pass")]


def test_bicep_external_container_app_is_direct_and_swa_is_static_frontend() -> None:
    assert _ingresses(BicepAdapter().scan(_BICEP, "main.bicep")) == [
        ("direct", None, "ACA ingress"),
        ("static_frontend", None, "SWA config"),
    ]


def test_bicep_internal_only_container_app_is_not_an_ingress() -> None:
    assert _ingresses(BicepAdapter().scan(_BICEP_INTERNAL_ONLY, "main.bicep")) == []


def test_terraform_ingress_roles() -> None:
    assert _ingresses(TerraformAdapter().scan(_TERRAFORM, "main.tf")) == [
        ("direct", None, "ACA ingress"),
        ("gateway", None, "API Management"),
        ("static_frontend", None, "SWA config"),
    ]


def test_k8s_ingress_and_externally_exposed_service() -> None:
    assert _ingresses(K8sAdapter().scan(_K8S, "k8s/app.yaml")) == [
        ("direct", None, "k8s Service LoadBalancer"),
        ("gateway", "bank.example.com", "k8s Ingress"),
    ]


def _doc_with(*entries: tuple[IngressRole, str]) -> AiSbomDocument:
    nodes = [
        Node(
            name=f"dep-{i}",
            component_type=ComponentType.DEPLOYMENT,
            confidence=1.0,
            metadata=NodeMetadata(ingresses=[IngressDetail(role=role, evidence=evidence)]),
        )
        for i, (role, evidence) in enumerate(entries)
    ]
    return AiSbomDocument(target="t", nodes=nodes)


def test_split_ingress_needs_both_a_gateway_and_a_direct_backend() -> None:
    assert has_split_ingress(_doc_with(("gateway", "nginx proxy_pass"), ("direct", "ACA ingress")))
    assert not has_split_ingress(_doc_with(("gateway", "nginx proxy_pass")))
    assert not has_split_ingress(_doc_with(("direct", "ACA ingress"), ("static_frontend", "SWA config")))


def test_collect_ingresses_dedupes_across_deployment_nodes() -> None:
    doc = _doc_with(("gateway", "nginx proxy_pass"), ("gateway", "nginx proxy_pass"))
    assert len(collect_ingresses(doc)) == 1


def test_ingresses_flow_through_the_extractor_into_node_metadata(tmp_path: Path) -> None:
    (tmp_path / "nginx.conf").write_text(_NGINX, encoding="utf-8")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.bicep").write_text(_BICEP, encoding="utf-8")
    # Default config: nginx configs are scanned even though ".conf" is not a source extension.
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))

    roles = {i.role for i in collect_ingresses(doc)}
    assert {"gateway", "direct", "static_frontend"} <= roles
    assert has_split_ingress(doc)
    # Round-trips through the serialized document.
    restored = AiSbomDocument.model_validate(doc.model_dump(mode="json"))
    assert has_split_ingress(restored)
