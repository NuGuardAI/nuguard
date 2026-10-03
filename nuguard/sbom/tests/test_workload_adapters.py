"""Workload facts from docker-compose, Kubernetes, Helm/Kustomize, Bicep, Terraform, CFN/SAM."""
# Detections carry loosely-typed metadata dicts; tests index them directly.
# mypy: disable-error-code="index,union-attr,call-overload"

from __future__ import annotations

import json
from typing import Any

from nuguard.sbom.adapters.azure_yaml import AzureYamlAdapter, is_azure_yaml
from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.bicep_workloads import bicep_workloads
from nuguard.sbom.adapters.cloud_workloads import ServiceDescriptorAdapter, cfn_workloads, terraform_workloads
from nuguard.sbom.adapters.compose import ComposeAdapter, is_compose_file
from nuguard.sbom.adapters.iac import K8sAdapter, _try_load_yaml
from nuguard.sbom.types import ComponentType


def _workloads(dets: list[ComponentDetection]) -> dict[str, dict[str, Any]]:
    return {
        d.display_name: d.metadata["workload"]
        for d in dets
        if d.component_type == ComponentType.DEPLOYMENT and "workload" in d.metadata
    }


def _by_name(dets: list[ComponentDetection], name: str) -> ComponentDetection:
    return next(d for d in dets if d.display_name == name and d.component_type == ComponentType.DEPLOYMENT)


# ============================================================================ compose
_COMPOSE = """
services:
  redis:
    image: redis:7.4-alpine
    ports: ["6379:6379"]
  api:
    build: { context: ./src/api, dockerfile: Dockerfile.prod }
    ports:
      - "127.0.0.1:8080:80"
      - target: 9000
        published: 9001
        protocol: udp
    expose: ["5000"]
    environment:
      - REDIS_URL=redis://redis:6379/0
      - DB_PASSWORD=hunter2
      - TOKEN=${TOKEN}
    depends_on:
      redis: { condition: service_healthy }
    deploy:
      replicas: 3
      resources:
        limits: { cpus: "0.5", memory: 512M }
        reservations: { memory: 256M }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:80/healthz"]
    user: root
    privileged: true
    volumes: ["/var/run/docker.sock:/var/run/docker.sock"]
    secrets: [db_pass]
  worker:
    image: ${WORKER_IMAGE}
secrets:
  db_pass: { file: ./db_pass.txt }
"""


def test_compose_file_name_detection() -> None:
    for name in ("docker-compose.yml", "compose.yaml", "deploy/docker-compose.prod.yml", "compose.override.yml"):
        assert is_compose_file(name)
    assert not is_compose_file("docker-compose-notes.md")
    assert not is_compose_file("values.yaml")


def test_compose_service_workload_facts() -> None:
    dets = ComposeAdapter().scan(_COMPOSE, "deploy/docker-compose.yml")
    wl = _workloads(dets)
    api = wl["api"]
    assert api["workload_kind"] == "compose_service"
    assert api["build_context"] == "deploy/src/api"
    assert api["dockerfile"] == "deploy/src/api/Dockerfile.prod"
    assert api["scaling"] == {"replicas": 3}
    assert api["resources"] == {"cpu_limit": "0.5", "memory_limit": "512M", "memory_request": "256M"}
    assert api["depends_on"] == ["redis"]
    assert api["secret_refs"] == ["db_pass"]
    assert api["probes"] == ["healthcheck:/healthz"]
    ports = {(p["container_port"], p["exposure"], p["protocol"]) for p in api["ports"]}
    assert ports == {(80, "internal", "tcp"), (9000, "public", "udp"), (5000, "cluster", "tcp")}
    assert api["internal_only"] is False


def test_compose_records_names_never_values() -> None:
    dets = ComposeAdapter().scan(_COMPOSE, "docker-compose.yml")
    api = _by_name(dets, "api")
    assert api.metadata["workload"]["env_var_names"] == ["DB_PASSWORD", "REDIS_URL", "TOKEN"]
    blob = json.dumps(api.metadata, default=str)
    assert "hunter2" not in blob


def test_compose_security_findings() -> None:
    api = _by_name(ComposeAdapter().scan(_COMPOSE, "docker-compose.yml"), "api")
    assert set(api.metadata["security_findings"]) == {
        "secrets_in_env_vars", "privileged_container", "docker_socket_mount",
    }
    assert api.metadata["runs_as_root"] is True
    assert api.metadata["has_resource_limits"] is True
    assert api.metadata["has_health_check"] is True


def test_compose_pulled_image_runs_hint_and_templated_image_skipped() -> None:
    dets = ComposeAdapter().scan(_COMPOSE, "docker-compose.yml")
    redis = _by_name(dets, "redis")
    assert [(r.relationship_type, r.target_canonical) for r in redis.relationships] == [
        ("RUNS", "container_image:redis:7.4-alpine")
    ]
    image = next(d for d in dets if d.component_type == ComponentType.CONTAINER_IMAGE)
    assert (image.metadata["os_name"], image.metadata["image_role"]) == ("alpine", "base")
    # built service and unresolved ${WORKER_IMAGE}: no image node, no RUNS hint
    assert _by_name(dets, "api").relationships == []
    assert _by_name(dets, "worker").relationships == []
    assert len([d for d in dets if d.component_type == ComponentType.CONTAINER_IMAGE]) == 1


def test_compose_env_value_service_references_become_depends_on() -> None:
    dets = ComposeAdapter().scan(_COMPOSE, "docker-compose.yml")
    assert _workloads(dets)["api"]["depends_on"] == ["redis"]  # via depends_on and REDIS_URL, deduped


def test_compose_non_compose_yaml_ignored() -> None:
    assert ComposeAdapter().scan("foo: bar\n", "docker-compose.yml") == []
    assert ComposeAdapter().scan("services: [", "docker-compose.yml") == []


# ============================================================================ Kubernetes
_K8S = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: api, namespace: shop}
spec:
  replicas: 3
  template:
    metadata: {labels: {app: api, tier: web}}
    spec:
      serviceAccountName: api-sa
      containers:
        - name: api
          image: ghcr.io/acme/api:1.4
          ports: [{containerPort: 8080}]
          env:
            - {name: LOG_LEVEL, value: debug}
            - name: DB_PASSWORD
              valueFrom: {secretKeyRef: {name: db-secret, key: pw}}
          envFrom: [{secretRef: {name: api-extra}}]
          resources:
            requests: {cpu: 100m, memory: 128Mi}
            limits: {cpu: 500m, memory: 256Mi}
          livenessProbe: {httpGet: {path: /healthz, port: 8080}}
          readinessProbe: {tcpSocket: {port: 8080}}
---
apiVersion: v1
kind: Service
metadata: {name: api-svc, namespace: shop}
spec:
  type: ClusterIP
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
  metrics:
    - type: Resource
      resource: {name: cpu, target: {type: Utilization, averageUtilization: 70}}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: web, namespace: shop}
spec:
  rules:
    - host: shop.example.com
      http:
        paths:
          - {path: /api, pathType: Prefix, backend: {service: {name: api-svc, port: {number: 80}}}}
"""


def test_k8s_workload_facts() -> None:
    dets = K8sAdapter().scan(_K8S, "k8s/app.yaml")
    api = _by_name(dets, "api")
    wl = api.metadata["workload"]
    assert wl["workload_kind"] == "k8s_deployment" and wl["namespace"] == "shop"
    assert wl["scaling"] == {"replicas": 3}
    assert wl["image_refs"] == ["ghcr.io/acme/api:1.4"]
    assert wl["ports"] == [
        {"container_port": 8080, "protocol": "tcp", "exposure": "cluster", "source": "k8s containerPort"}
    ]
    assert wl["resources"] == {
        "cpu_request": "100m", "cpu_limit": "500m", "memory_request": "128Mi", "memory_limit": "256Mi",
    }
    assert wl["identity_ref"] == "api-sa"
    assert wl["env_var_names"] == ["DB_PASSWORD", "LOG_LEVEL"]
    assert wl["secret_refs"] == ["api-extra", "db-secret"]
    assert wl["probes"] == ["liveness:/healthz", "readiness:tcp:8080"]
    assert api.metadata["k8s_pod_labels"] == {"app": "api", "tier": "web"}
    assert api.metadata["cloud_provider"] == "kubernetes"
    assert [(r.relationship_type, r.target_canonical) for r in api.relationships] == [
        ("RUNS", "container_image:ghcr.io/acme/api:1.4")
    ]


def test_k8s_service_hpa_and_ingress_facts() -> None:
    dets = K8sAdapter().scan(_K8S, "k8s/app.yaml")
    svc = next(d for d in dets if d.canonical_name == "fact:k8s:service:shop:api-svc")
    assert svc.metadata["fact_marker"] is True
    assert svc.metadata["k8s_service"]["selector"] == {"app": "api"}
    assert svc.metadata["k8s_service"]["ports"][0]["target_port"] == 8080
    hpa = next(d for d in dets if "hpa" in d.canonical_name)
    assert hpa.metadata["k8s_hpa"]["target_name"] == "api"
    assert (hpa.metadata["k8s_hpa"]["min_replicas"], hpa.metadata["k8s_hpa"]["max_replicas"]) == (2, 10)
    assert hpa.metadata["k8s_hpa"]["metrics"] == ["cpu:70%"]
    ing = next(d for d in dets if d.canonical_name.startswith("ingress:k8s"))
    assert ing.metadata["k8s_ingress_rules"] == [
        {"host": "shop.example.com", "path": "/api", "service": "api-svc", "port": 80}
    ]
    assert ing.metadata["ingresses"][0]["role"] == "gateway"


def test_k8s_loadbalancer_service_keeps_ingress_and_carries_service_fact() -> None:
    manifest = """
apiVersion: v1
kind: Service
metadata: {name: lb, namespace: d}
spec:
  type: LoadBalancer
  selector: {app: x}
  ports: [{port: 443, targetPort: 8443}]
"""
    (det,) = K8sAdapter().scan(manifest, "svc.yaml")
    assert det.canonical_name == "ingress:k8s:d:service:lb"
    assert det.metadata["ingresses"][0]["role"] == "direct"
    assert det.metadata["k8s_service"]["type"] == "LoadBalancer"
    assert "fact_marker" not in det.metadata


def test_k8s_cronjob_pod_spec_and_keda() -> None:
    cron = """
apiVersion: batch/v1
kind: CronJob
metadata: {name: nightly}
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers: [{name: j, image: "acme/job:2"}]
"""
    (det, *_) = K8sAdapter().scan(cron, "cron.yaml")
    assert det.metadata["workload"]["image_refs"] == ["acme/job:2"]
    keda = """
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata: {name: s}
spec:
  scaleTargetRef: {name: worker}
  minReplicaCount: 0
  maxReplicaCount: 20
  triggers: [{type: rabbitmq}]
"""
    hpa = K8sAdapter().scan(keda, "keda.yaml")[0].metadata["k8s_hpa"]
    assert (hpa["autoscaler"], hpa["min_replicas"], hpa["max_replicas"]) == ("keda", 0, 20)


def test_helm_values_workload() -> None:
    values = """
replicaCount: 2
image: {repository: ghcr.io/acme/web, tag: "3.1"}
service: {type: LoadBalancer, port: 80}
containerPort: 8080
autoscaling: {enabled: true, minReplicas: 2, maxReplicas: 8, targetCPUUtilizationPercentage: 65}
resources: {limits: {cpu: 1, memory: 1Gi}}
"""
    dets = K8sAdapter().scan(values, "charts/web/values.yaml")
    chart = next(d for d in dets if d.component_type == ComponentType.DEPLOYMENT)
    assert chart.canonical_name == "deployment:helm:charts/web"
    wl = chart.metadata["workload"]
    assert wl["source_format"] == "helm_values"
    assert wl["image_refs"] == ["ghcr.io/acme/web:3.1"]
    assert wl["scaling"] == {"min_replicas": 2, "max_replicas": 8, "autoscaler": "hpa", "metric": "cpu", "target": "65%"}
    assert wl["ports"][0]["exposure"] == "public" and wl["internal_only"] is False
    assert K8sAdapter().scan("foo: bar\n", "charts/x/values.yaml") == []


def test_kustomize_fact() -> None:
    kz = """
namespace: prod
images: [{name: acme/api, newTag: "2.0"}]
replicas: [{name: api, count: 5}]
resources: [../base]
"""
    (det,) = K8sAdapter().scan(kz, "overlays/prod/kustomization.yaml")
    fact = det.metadata["kustomize"]
    assert fact["directory"] == "overlays/prod" and fact["namespace"] == "prod"
    assert fact["images"] == [{"name": "acme/api", "new_name": None, "new_tag": "2.0"}]
    assert fact["replicas"] == [{"name": "api", "count": 5}]
    assert det.metadata["fact_marker"] is True


# ============================================================================ Bicep
_BICEP = """
param location string = 'eastus'
var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var svcs = [
  'svc-a'
  'svc-b'
]
var sharedEnv = [
  { name: 'SHARED', value: 'x' }
  { name: 'URL_B', value: 'http://svc-b' }
]
resource acaEnv 'Microsoft.App/managedEnvironments@2023-05-01' = { name: 'env' }

resource gateway 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'gateway'
  tags: union(tags, {
    'azd-service-name': 'gateway'
  })
  identity: { type: 'SystemAssigned' }
  properties: {
    configuration: {
      ingress: { external: true, targetPort: 8001 }
      secrets: [ { name: 'api-key', value: 'x' } ]
    }
    template: {
      containers: [
        {
          name: 'gateway'
          image: placeholderImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(sharedEnv, [ { name: 'OPENAI_API_KEY', secretRef: 'api-key' } ])
          probes: [ { type: 'Liveness', httpGet: { path: '/health', port: 8001 } } ]
        }
      ]
      scale: {
        minReplicas: 1  // keep warm
        maxReplicas: 5
      }
    }
  }
}

resource apps 'Microsoft.App/containerApps@2023-05-01' = [for s in svcs: {
  name: s
  tags: { 'azd-service-name': s }
  properties: {
    configuration: { ingress: { external: false, targetPort: 8080 } }
    template: {
      containers: [ { name: s, image: 'acr.io/team/${s}:1', env: sharedEnv } ]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}]
"""


def test_bicep_container_apps_and_loop_expansion() -> None:
    dets = bicep_workloads(_BICEP, "infra/aca.bicep")
    wl = _workloads(dets)
    assert set(wl) == {"gateway", "svc-a", "svc-b"}
    gw = wl["gateway"]
    assert gw["cloud_service"] == "azure_container_apps"
    assert gw["ports"] == [{"container_port": 8001, "protocol": "tcp", "exposure": "public", "source": "aca ingress"}]
    assert gw["scaling"] == {"min_replicas": 1, "max_replicas": 5, "autoscaler": "aca_rule"}
    assert gw["resources"] == {"cpu_limit": "0.5", "memory_limit": "1Gi"}
    assert gw["identity_ref"] == "system-assigned"
    assert gw["env_var_names"] == ["OPENAI_API_KEY", "SHARED", "URL_B"]
    assert gw["secret_refs"] == ["api-key"]
    assert gw["probes"] == ["liveness:/health"]
    assert gw["internal_only"] is False
    assert wl["svc-a"]["internal_only"] is True
    assert wl["svc-a"]["ports"][0]["exposure"] == "internal"


def test_bicep_placeholder_image_not_linked_and_azd_tag_resolved() -> None:
    dets = bicep_workloads(_BICEP, "infra/aca.bicep")
    gateway = _by_name(dets, "gateway")
    assert "image_refs" not in gateway.metadata["workload"]
    assert gateway.relationships == []
    assert gateway.metadata["azd_service_name"] == "gateway"
    assert _by_name(dets, "svc-b").metadata["azd_service_name"] == "svc-b"
    # real image with interpolation of the loop variable is left unresolved, not guessed
    assert not any(d.component_type == ComponentType.CONTAINER_IMAGE for d in dets)


def test_bicep_env_url_references_become_depends_on() -> None:
    wl = _workloads(bicep_workloads(_BICEP, "infra/aca.bicep"))
    assert wl["gateway"]["depends_on"] == ["svc-b"]
    assert wl["svc-a"]["depends_on"] == ["svc-b"]
    assert "depends_on" not in wl["svc-b"]


def test_bicep_other_compute_types() -> None:
    bicep = """
resource site 'Microsoft.Web/sites@2022-03-01' = {
  name: 'web'
  properties: { siteConfig: { linuxFxVersion: 'DOCKER|acr.io/web:2' }, publicNetworkAccess: 'Disabled' }
}
resource aks 'Microsoft.ContainerService/managedClusters@2023-05-01' = {
  name: 'k'
  properties: { agentPoolProfiles: [ { count: 3, minCount: 2, maxCount: 6 } ], apiServerAccessProfile: { enablePrivateCluster: true } }
}
"""
    wl = _workloads(bicep_workloads(bicep, "i.bicep"))
    assert wl["web"]["image_refs"] == ["acr.io/web:2"] and wl["web"]["internal_only"] is True
    assert wl["k"]["scaling"] == {"replicas": 3, "min_replicas": 2, "max_replicas": 6, "autoscaler": "hpa"}


def test_azure_yaml_services() -> None:
    assert is_azure_yaml("azure.yaml") and not is_azure_yaml("values.yaml")
    azd = """
name: app
services:
  web:
    project: ./src/web
    host: containerapp
    docker: {path: Dockerfile, context: .}
"""
    (det,) = AzureYamlAdapter().scan(azd, "azure.yaml")
    fact = det.metadata["azd_service"]
    assert fact["project"] == "src/web" and fact["dockerfile"] == "src/web/Dockerfile"
    assert det.metadata["fact_marker"] is True
    assert AzureYamlAdapter().scan("services: {}\n", "azure.yaml") == []


# ============================================================================ Terraform
_TF = """
variable "image" { default = "ghcr.io/acme/web:1.2" }
resource "aws_ecs_task_definition" "api" {
  family = "api"
  cpu = "512"
  memory = "1024"
  container_definitions = jsonencode([{ name = "api", image = "123.dkr.ecr.us-east-1.amazonaws.com/api:v1", portMappings = [{ containerPort = 8080 }], environment = [{ name = "DB_URL", value = "x" }], secrets = [{ name = "DB_PASSWORD", valueFrom = "arn:aws:secretsmanager:x" }] }])
}
resource "aws_ecs_service" "api" {
  name = "api"
  task_definition = aws_ecs_task_definition.api.arn
  desired_count = 2
  network_configuration { assign_public_ip = true }
}
resource "aws_appautoscaling_target" "api" {
  min_capacity = 2
  max_capacity = 10
  resource_id = "service/c/${aws_ecs_service.api.name}"
}
resource "google_cloud_run_v2_service" "web" {
  name = "web"
  ingress = "INGRESS_TRAFFIC_ALL"
  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
    containers {
      image = var.image
      ports { container_port = 3000 }
    }
  }
}
resource "google_cloud_run_v2_service_iam_member" "pub" {
  name = google_cloud_run_v2_service.web.name
  member = "allUsers"
}
resource "aws_lambda_function" "fn" {
  function_name = "fn"
  runtime = "python3.12"
  memory_size = 256
  environment { variables = { LOG_LEVEL = "info", API_KEY = "x" } }
}
resource "aws_lambda_function_url" "u" {
  function_name = aws_lambda_function.fn.function_name
  authorization_type = "NONE"
}
resource "azurerm_container_app" "aca" {
  name = "aca"
  template {
    min_replicas = 1
    max_replicas = 3
    container { name = "c" image = "acr.io/aca:1" cpu = 0.25 memory = "0.5Gi" }
  }
  ingress { external_enabled = true target_port = 80 }
}
"""


def test_terraform_ecs_service_with_task_definition_and_autoscaling() -> None:
    wl = _workloads(terraform_workloads(_TF, "infra/main.tf"))["api"]
    assert wl["workload_kind"] == "ecs_service" and wl["cloud_service"] == "aws_ecs"
    assert wl["image_refs"] == ["123.dkr.ecr.us-east-1.amazonaws.com/api:v1"]
    assert wl["scaling"] == {"replicas": 2, "min_replicas": 2, "max_replicas": 10, "autoscaler": "ecs_autoscaling"}
    assert wl["ports"][0]["container_port"] == 8080 and wl["ports"][0]["exposure"] == "public"
    assert wl["resources"] == {"cpu_limit": "512", "memory_limit": "1024"}
    assert wl["env_var_names"] == ["DB_PASSWORD", "DB_URL"]
    assert wl["secret_refs"] == ["arn:aws:secretsmanager:x"]


def test_terraform_cloud_run_public_invoker_and_var_default() -> None:
    dets = terraform_workloads(_TF, "infra/main.tf")
    web = _by_name(dets, "web")
    assert web.metadata["security_findings"] == ["unauthenticated_invoker"]
    wl = web.metadata["workload"]
    assert wl["image_refs"] == ["ghcr.io/acme/web:1.2"]  # var.image default resolved
    assert wl["scaling"] == {"min_replicas": 0, "max_replicas": 5, "autoscaler": "cloud_run"}
    assert wl["ports"][0] == {
        "container_port": 3000, "protocol": "tcp", "exposure": "public", "source": "cloud run ingress",
    }


def test_terraform_lambda_function_url_without_auth() -> None:
    fn = _by_name(terraform_workloads(_TF, "infra/main.tf"), "fn")
    assert fn.metadata["security_findings"] == ["unauthenticated_function_url"]
    wl = fn.metadata["workload"]
    assert wl["env_var_names"] == ["API_KEY", "LOG_LEVEL"]
    assert wl["internal_only"] is False
    assert wl["resources"] == {"memory_limit": "256"}


def test_terraform_container_app() -> None:
    wl = _workloads(terraform_workloads(_TF, "infra/main.tf"))["aca"]
    assert wl["scaling"] == {"min_replicas": 1, "max_replicas": 3, "autoscaler": "aca_rule"}
    assert wl["ports"][0]["exposure"] == "public" and wl["image_refs"] == ["acr.io/aca:1"]


# ============================================================================ CloudFormation / SAM
_CFN = """
AWSTemplateFormatVersion: "2010-09-09"
Transform: AWS::Serverless-2016-10-31
Resources:
  TD:
    Type: AWS::ECS::TaskDefinition
    Properties:
      Cpu: "256"
      Memory: "512"
      TaskRoleArn: !GetAtt Role.Arn
      ContainerDefinitions:
        - Name: web
          Image: nginx:1.25
          PortMappings: [{ContainerPort: 80}]
          Environment: [{Name: MODE, Value: prod}]
  Svc:
    Type: AWS::ECS::Service
    Properties:
      DesiredCount: 3
      TaskDefinition: !Ref TD
      LoadBalancers: [{ContainerName: web, ContainerPort: 80}]
  Scale:
    Type: AWS::ApplicationAutoScaling::ScalableTarget
    Properties:
      MinCapacity: 3
      MaxCapacity: 12
      ResourceId: !Sub "service/cluster/${Svc.Name}"
  Fn:
    Type: AWS::Serverless::Function
    Properties:
      Runtime: python3.12
      Environment: {Variables: {STAGE: prod}}
      Events:
        Get: {Type: Api, Properties: {Path: /items, Method: get}}
  Open:
    Type: AWS::Lambda::Function
    Properties:
      FunctionUrlConfig: {AuthType: NONE}
"""


def test_cloudformation_yaml_with_intrinsic_tags_parses() -> None:
    data = _try_load_yaml(_CFN)
    # scalar tags resolve to plain strings so string-typed adapter code keeps working
    assert data["Resources"]["Svc"]["Properties"]["TaskDefinition"] == "TD"
    assert data["Resources"]["TD"]["Properties"]["TaskRoleArn"] == "Role.Arn"
    assert _try_load_yaml("a: !Join [',', [x, y]]\n") == {"a": "<Fn::Join>"}


def test_cfn_ecs_service_and_sam_function() -> None:
    dets = cfn_workloads(_try_load_yaml(_CFN), "t.yaml")
    wl = _workloads(dets)
    svc = wl["Svc"]
    assert svc["image_refs"] == ["nginx:1.25"]
    assert svc["scaling"] == {"replicas": 3, "min_replicas": 3, "max_replicas": 12, "autoscaler": "ecs_autoscaling"}
    assert svc["ports"][0] == {"container_port": 80, "protocol": "tcp", "exposure": "public", "source": "ecs portMappings"}
    assert svc["env_var_names"] == ["MODE"]
    fn = _by_name(dets, "Fn")
    assert fn.metadata["sam_api_routes"] == ["get /items"]
    assert wl["Fn"]["internal_only"] is False
    assert wl["Fn"]["env_var_names"] == ["STAGE"]


def test_cfn_unauthenticated_function_url_flagged() -> None:
    dets = cfn_workloads(_try_load_yaml(_CFN), "t.yaml")
    assert _by_name(dets, "Open").metadata["security_findings"] == ["unauthenticated_function_url"]


# ============================================================================ standalone descriptors
def test_knative_cloud_run_service_yaml() -> None:
    ks = """
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: hello
  annotations:
    run.googleapis.com/ingress: internal
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "1"
        autoscaling.knative.dev/maxScale: "10"
    spec:
      serviceAccountName: runner
      containers:
        - image: gcr.io/p/hello:1
          ports: [{containerPort: 8080}]
          env: [{name: MODE, value: x}]
          resources: {limits: {cpu: "1", memory: 512Mi}}
"""
    dets = ServiceDescriptorAdapter().scan(ks, "svc.yaml")
    wl = _workloads(dets)["hello"]
    assert wl["workload_kind"] == "cloud_run" and wl["internal_only"] is True
    assert wl["scaling"] == {"min_replicas": 1, "max_replicas": 10, "autoscaler": "cloud_run"}
    assert wl["identity_ref"] == "runner" and wl["env_var_names"] == ["MODE"]
    assert wl["image_refs"] == ["gcr.io/p/hello:1"]
    assert ServiceDescriptorAdapter().scan("kind: Service\nmetadata: {name: x}\n", "svc.yaml") == []


def test_ecs_task_definition_json() -> None:
    td = {
        "family": "worker",
        "cpu": "256",
        "memory": "512",
        "containerDefinitions": [
            {"name": "w", "image": "x/y:1", "portMappings": [{"containerPort": 9}],
             "environment": [{"name": "A", "value": "1"}], "secrets": [{"name": "S", "valueFrom": "arn"}]}
        ],
    }
    dets = ServiceDescriptorAdapter().scan(json.dumps(td), "task-def.json")
    wl = _workloads(dets)["worker"]
    assert wl["workload_kind"] == "ecs_task_definition"
    assert wl["image_refs"] == ["x/y:1"] and wl["env_var_names"] == ["A"] and wl["secret_refs"] == ["S"]
    assert ServiceDescriptorAdapter().scan(json.dumps({"other": 1}), "x.json") == []
