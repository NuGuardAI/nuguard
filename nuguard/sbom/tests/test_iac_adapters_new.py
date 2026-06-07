"""Tests for new features in K8sAdapter and GitHubActionsAdapter (iac.py).

Covers:
- K8sAdapter: multi-document YAML parsing, same-file NetworkPolicy coverage,
  cross-namespace non-coverage, standalone NetworkPolicy marker emission.
- GitHubActionsAdapter: structured security findings for NGA-010/011/014,
  clean workflow produces no findings, workflow_content stored in node metadata.
"""

from __future__ import annotations

from typing import Any

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.iac import GitHubActionsAdapter, K8sAdapter

# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

_DEPLOYMENT_YAML = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ai-service
  namespace: {namespace}
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: app
          image: myapp:1.0
          resources:
            limits:
              memory: "512Mi"
"""

_NETWORK_POLICY_YAML = """\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-internal
  namespace: {namespace}
spec:
  podSelector: {{}}
  policyTypes:
    - Ingress
"""

# ---------------------------------------------------------------------------
# GHA workflow YAML fixtures
# ---------------------------------------------------------------------------

_CLEAN_WORKFLOW = """\
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "hello world"
"""

_PR_TARGET_INJECTION_WORKFLOW = """\
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo ${{ github.event.pull_request.head.sha }}
"""

_ENV_INJECTION_WORKFLOW = """\
on:
  issues:
    types: [opened]
jobs:
  triage:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event.issue.title }}" >> $GITHUB_ENV
"""

_DEBUG_SECRET_WORKFLOW = """\
on:
  push:
jobs:
  debug:
    runs-on: ubuntu-latest
    env:
      ACTIONS_RUNNER_DEBUG: ${{ secrets.ACTIONS_RUNNER_DEBUG }}
    steps:
      - run: echo "debugging"
"""


# ---------------------------------------------------------------------------
# K8sAdapter — multi-document YAML
# ---------------------------------------------------------------------------


class TestK8sAdapterMultiDoc:
    adapter = K8sAdapter()

    def test_single_doc_workload(self) -> None:
        """Single Deployment YAML → one DEPLOYMENT node, no has_network_policy."""
        content = _DEPLOYMENT_YAML.format(namespace="default")
        results = self.adapter.scan(content, "k8s/deployment.yaml")
        deployments = [r for r in results if r.metadata.get("deployment_target") == "kubernetes"]
        assert len(deployments) == 1
        assert deployments[0].display_name == "ai-service"
        assert deployments[0].metadata.get("has_network_policy") is not True

    def test_multi_doc_same_namespace_covered(self) -> None:
        """Deployment + NetworkPolicy in same namespace → workload has has_network_policy=True."""
        content = (
            _DEPLOYMENT_YAML.format(namespace="default")
            + "---\n"
            + _NETWORK_POLICY_YAML.format(namespace="default")
        )
        results = self.adapter.scan(content, "k8s/manifest.yaml")

        workloads = [
            r for r in results
            if r.metadata.get("deployment_target") == "kubernetes"
            and not r.metadata.get("is_network_policy_namespace")
        ]
        assert len(workloads) == 1
        assert workloads[0].metadata.get("has_network_policy") is True

        # Marker node emitted for the NetworkPolicy namespace
        markers = [r for r in results if r.metadata.get("is_network_policy_namespace")]
        assert len(markers) == 1
        assert markers[0].metadata.get("k8s_namespace") == "default"

    def test_multi_doc_different_namespace_not_covered(self) -> None:
        """NetworkPolicy in 'other' namespace, Deployment in 'default' → NOT covered."""
        content = (
            _DEPLOYMENT_YAML.format(namespace="default")
            + "---\n"
            + _NETWORK_POLICY_YAML.format(namespace="other")
        )
        results = self.adapter.scan(content, "k8s/manifest.yaml")

        workloads = [
            r for r in results
            if r.metadata.get("deployment_target") == "kubernetes"
            and not r.metadata.get("is_network_policy_namespace")
        ]
        assert len(workloads) == 1
        assert workloads[0].metadata.get("has_network_policy") is not True

    def test_standalone_network_policy_emits_marker(self) -> None:
        """YAML with only a NetworkPolicy → one marker DEPLOYMENT node emitted."""
        content = _NETWORK_POLICY_YAML.format(namespace="prod")
        results = self.adapter.scan(content, "k8s/netpol.yaml")

        markers = [r for r in results if r.metadata.get("is_network_policy_namespace")]
        assert len(markers) == 1
        assert markers[0].metadata.get("k8s_namespace") == "prod"
        # No regular workload nodes
        workloads = [
            r for r in results
            if r.metadata.get("deployment_target") == "kubernetes"
            and not r.metadata.get("is_network_policy_namespace")
        ]
        assert workloads == []

    def test_non_k8s_yaml_returns_empty(self) -> None:
        """Plain YAML without apiVersion/kind → returns []."""
        content = "name: my-config\nvalue: 42\n"
        results = self.adapter.scan(content, "config.yaml")
        assert results == []


# ---------------------------------------------------------------------------
# GitHubActionsAdapter — structured security findings
# ---------------------------------------------------------------------------


class TestGitHubActionsAdapterFindings:
    adapter = GitHubActionsAdapter()

    def _get_deployment_node(self, results: list[ComponentDetection]) -> ComponentDetection:
        """Return the first DEPLOYMENT node from adapter scan results."""
        nodes = [r for r in results if r.metadata.get("iac_format") == "github_actions"]
        assert nodes, "No github_actions DEPLOYMENT node found in results"
        return nodes[0]

    def test_clean_workflow_no_findings(self) -> None:
        """Minimal valid workflow with no unsafe patterns → workflow_security_findings is []."""
        results = self.adapter.scan(_CLEAN_WORKFLOW, ".github/workflows/ci.yml")
        node = self._get_deployment_node(results)
        findings: list[dict[str, Any]] = node.metadata.get("workflow_security_findings", [])
        assert findings == []

    def test_detects_pr_target_injection(self) -> None:
        """pull_request_target + ${{ github.event.pull_request.* }} → NGA-010 entry."""
        results = self.adapter.scan(
            _PR_TARGET_INJECTION_WORKFLOW, ".github/workflows/pr.yml"
        )
        node = self._get_deployment_node(results)
        findings: list[dict[str, Any]] = node.metadata.get("workflow_security_findings", [])
        nga010 = [f for f in findings if f.get("rule_signal") == "NGA-010"]
        assert len(nga010) == 1
        assert nga010[0]["path"] == ".github/workflows/pr.yml"
        assert nga010[0]["line"] >= 1

    def test_detects_env_injection(self) -> None:
        """echo '...${{ ... }}...' >> $GITHUB_ENV → NGA-011 entry."""
        results = self.adapter.scan(
            _ENV_INJECTION_WORKFLOW, ".github/workflows/triage.yml"
        )
        node = self._get_deployment_node(results)
        findings: list[dict[str, Any]] = node.metadata.get("workflow_security_findings", [])
        nga011 = [f for f in findings if f.get("rule_signal") == "NGA-011"]
        assert len(nga011) == 1
        assert "GITHUB_ENV" in nga011[0]["snippet"]

    def test_detects_debug_secret(self) -> None:
        """ACTIONS_RUNNER_DEBUG reference → NGA-014 entry."""
        results = self.adapter.scan(
            _DEBUG_SECRET_WORKFLOW, ".github/workflows/debug.yml"
        )
        node = self._get_deployment_node(results)
        findings: list[dict[str, Any]] = node.metadata.get("workflow_security_findings", [])
        nga014 = [f for f in findings if f.get("rule_signal") == "NGA-014"]
        assert len(nga014) == 1
        assert "ACTIONS_RUNNER_DEBUG" in nga014[0]["snippet"]

    def test_workflow_content_stored(self) -> None:
        """Any valid GHA workflow → workflow_content key present in node metadata."""
        results = self.adapter.scan(_CLEAN_WORKFLOW, ".github/workflows/ci.yml")
        node = self._get_deployment_node(results)
        assert "workflow_content" in node.metadata
        assert isinstance(node.metadata["workflow_content"], str)
        assert len(node.metadata["workflow_content"]) > 0
