"""Test the Kubernetes YAML scanner."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nuguard.models.sbom import NodeType
from nuguard.sbom.adapters.iac import K8sAdapter as KubernetesScanner

K8S_DEPLOYMENT_YAML = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
        - name: app
          image: my-app:1.0.0
          resources:
            limits:
              cpu: "500m"
              memory: "256Mi"
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
"""

K8S_ROOT_CONTAINER_YAML = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: root-app
spec:
  template:
    spec:
      securityContext:
        runAsUser: 0
      containers:
        - name: app
          image: ubuntu:latest
"""

K8S_SERVICE_ACCOUNT_YAML = """
apiVersion: v1
kind: ServiceAccount
metadata:
  name: my-service-account
  namespace: default
"""

K8S_CLUSTER_ROLE_YAML = """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: pod-reader
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
"""

K8S_SECRETS_IN_ENV_YAML = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: secret-app
spec:
  template:
    spec:
      containers:
        - name: app
          image: app:latest
          env:
            - name: DB_PASSWORD
              value: "mysecretpassword"
"""


@pytest.fixture
def scanner() -> KubernetesScanner:
    return KubernetesScanner()


def _write_yaml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
    f.write(content)
    f.close()
    return Path(f.name)


def test_deployment_node_created(scanner: KubernetesScanner) -> None:
    """Deployment manifest creates a DEPLOYMENT node."""
    path = _write_yaml(K8S_DEPLOYMENT_YAML)
    nodes, edges, findings = scanner.scan(path)
    dep_nodes = [n for n in nodes if n.component_type == NodeType.DEPLOYMENT]
    assert len(dep_nodes) >= 1
    assert any(n.name == "my-app" for n in dep_nodes)


def test_container_image_extracted(scanner: KubernetesScanner) -> None:
    """Container image reference creates a CONTAINER_IMAGE node."""
    path = _write_yaml(K8S_DEPLOYMENT_YAML)
    nodes, edges, findings = scanner.scan(path)
    img_nodes = [n for n in nodes if n.component_type == NodeType.CONTAINER_IMAGE]
    assert len(img_nodes) >= 1
    assert any(n.name == "my-app" for n in img_nodes)
    assert any(n.metadata.image_tag == "1.0.0" for n in img_nodes)


def test_runs_as_root_finding(scanner: KubernetesScanner) -> None:
    """Container with runAsUser=0 produces 'container_runs_as_root' finding."""
    path = _write_yaml(K8S_ROOT_CONTAINER_YAML)
    nodes, edges, findings = scanner.scan(path)
    assert "container_runs_as_root" in findings


def test_missing_health_check_finding(scanner: KubernetesScanner) -> None:
    """Container without liveness/readiness probe → 'missing_health_check' finding."""
    path = _write_yaml(K8S_ROOT_CONTAINER_YAML)
    nodes, edges, findings = scanner.scan(path)
    assert "missing_health_check" in findings


def test_no_resource_limits_finding(scanner: KubernetesScanner) -> None:
    """Container without resource limits → 'no_resource_limits' finding."""
    path = _write_yaml(K8S_ROOT_CONTAINER_YAML)
    nodes, edges, findings = scanner.scan(path)
    assert "no_resource_limits" in findings


def test_service_account_creates_iam_node(scanner: KubernetesScanner) -> None:
    """ServiceAccount creates an IAM node with k8s_service_account type."""
    path = _write_yaml(K8S_SERVICE_ACCOUNT_YAML)
    nodes, edges, findings = scanner.scan(path)
    iam_nodes = [n for n in nodes if n.component_type == NodeType.IAM]
    assert len(iam_nodes) >= 1
    sa = next(n for n in iam_nodes if n.name == "my-service-account")
    assert sa.metadata.iam_type == "k8s_service_account"


def test_cluster_role_creates_iam_node(scanner: KubernetesScanner) -> None:
    """ClusterRole creates an IAM node with permissions."""
    path = _write_yaml(K8S_CLUSTER_ROLE_YAML)
    nodes, edges, findings = scanner.scan(path)
    iam_nodes = [n for n in nodes if n.component_type == NodeType.IAM]
    assert any(n.name == "pod-reader" for n in iam_nodes)
    role_node = next(n for n in iam_nodes if n.name == "pod-reader")
    assert len(role_node.metadata.permissions) > 0


def test_secrets_in_env_finding(scanner: KubernetesScanner) -> None:
    """Env var with 'PASSWORD' name and literal value → 'secrets_in_env_vars' finding."""
    path = _write_yaml(K8S_SECRETS_IN_ENV_YAML)
    nodes, edges, findings = scanner.scan(path)
    assert "secrets_in_env_vars" in findings


def test_healthy_deployment_no_findings(scanner: KubernetesScanner) -> None:
    """Well-configured deployment has no security findings."""
    path = _write_yaml(K8S_DEPLOYMENT_YAML)
    nodes, edges, findings = scanner.scan(path)
    assert "container_runs_as_root" not in findings
    assert "missing_health_check" not in findings
    assert "no_resource_limits" not in findings
