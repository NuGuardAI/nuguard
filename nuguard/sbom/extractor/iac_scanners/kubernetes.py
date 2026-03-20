"""Kubernetes YAML manifest scanner.

Parses Kubernetes YAML files to extract DEPLOYMENT, IAM, and CONTAINER_IMAGE
nodes, and produce security findings.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from nuguard.models.sbom import (
    Edge,
    EdgeRelationshipType,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)

_SECRET_PATTERNS = re.compile(
    r"(password|secret|key|token|passwd|pwd|credential)",
    re.IGNORECASE,
)

_WORKLOAD_KINDS = frozenset(
    {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
)


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _ev(detail: str, path: Path, line: int | None = None) -> Evidence:
    return Evidence(
        kind=EvidenceKind.IAC,
        confidence=0.85,
        detail=detail,
        location=EvidenceLocation(path=str(path), line=line),
    )


class KubernetesScanner:
    """Scan a Kubernetes YAML manifest and extract graph nodes."""

    def scan(
        self, file_path: Path
    ) -> tuple[list[Node], list[Edge], list[str]]:
        """Return (nodes, edges, security_findings) for *file_path*.

        Args:
            file_path: Path to a Kubernetes YAML manifest.

        Returns:
            Tuple of nodes, edges, and a list of security finding strings.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return [], [], ["pyyaml_not_installed"]

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], [], []

        nodes: list[Node] = []
        edges: list[Edge] = []
        findings: list[str] = []

        try:
            docs = list(yaml.safe_load_all(source))
        except Exception:
            return [], [], []

        iam_nodes: dict[str, str] = {}  # resource name → node id

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind", "")
            meta = doc.get("metadata", {}) or {}
            name = meta.get("name", "unknown")

            if kind in _WORKLOAD_KINDS:
                dep_nodes, dep_findings = self._scan_workload(doc, name, file_path)
                nodes.extend(dep_nodes)
                findings.extend(dep_findings)

            elif kind == "ServiceAccount":
                nid = _stable_id(name, NodeType.IAM)
                iam_nodes[name] = nid
                nodes.append(
                    Node(
                        id=nid,
                        name=name,
                        component_type=NodeType.IAM,
                        confidence=0.9,
                        metadata=NodeMetadata(
                            iam_type="k8s_service_account",
                            principal=name,
                        ),
                        evidence=[_ev(f"Kubernetes ServiceAccount '{name}'", file_path)],
                    )
                )

            elif kind in ("ClusterRole", "Role"):
                nid = _stable_id(name, NodeType.IAM)
                iam_nodes[name] = nid
                perms = _extract_k8s_permissions(doc)
                nodes.append(
                    Node(
                        id=nid,
                        name=name,
                        component_type=NodeType.IAM,
                        confidence=0.85,
                        metadata=NodeMetadata(
                            iam_type=f"k8s_{kind.lower()}",
                            permissions=perms,
                        ),
                        evidence=[_ev(f"Kubernetes {kind} '{name}'", file_path)],
                    )
                )

            elif kind in ("ClusterRoleBinding", "RoleBinding"):
                # Create edge between subject and role
                subjects = doc.get("subjects") or []
                role_ref = doc.get("roleRef", {}) or {}
                role_name = role_ref.get("name", "")
                for subj in subjects:
                    if not isinstance(subj, dict):
                        continue
                    subj_name = subj.get("name", "")
                    src_id = _stable_id(subj_name, NodeType.IAM)
                    tgt_id = _stable_id(role_name, NodeType.IAM)
                    edges.append(
                        Edge(
                            source=src_id,
                            target=tgt_id,
                            relationship_type=EdgeRelationshipType.USES,
                        )
                    )

        return nodes, edges, list(dict.fromkeys(findings))

    def _scan_workload(
        self, doc: dict, name: str, file_path: Path
    ) -> tuple[list[Node], list[str]]:
        nodes: list[Node] = []
        findings: list[str] = []
        kind = doc.get("kind", "Deployment")

        spec = doc.get("spec", {}) or {}
        template = spec.get("template", {}) or {}
        pod_spec = template.get("spec", {}) or {}
        containers: list[dict] = pod_spec.get("containers", []) or []

        runs_as_root = False
        has_health_check = True
        has_resource_limits = True

        # Check security context
        pod_sec = pod_spec.get("securityContext", {}) or {}
        if pod_sec.get("runAsUser") == 0:
            runs_as_root = True

        for container in containers:
            if not isinstance(container, dict):
                continue
            sec = container.get("securityContext", {}) or {}
            if sec.get("runAsUser") == 0:
                runs_as_root = True

            if not container.get("livenessProbe") and not container.get("readinessProbe"):
                has_health_check = False

            resources = container.get("resources", {}) or {}
            if not resources.get("limits"):
                has_resource_limits = False

            # Check env for secrets
            env = container.get("env", []) or []
            for env_var in env:
                if not isinstance(env_var, dict):
                    continue
                env_name = env_var.get("name", "")
                env_val = env_var.get("value")
                if (
                    env_val is not None
                    and isinstance(env_val, str)
                    and _SECRET_PATTERNS.search(env_name)
                ):
                    findings.append("secrets_in_env_vars")

            # Container image
            image_ref = container.get("image", "")
            if image_ref:
                image_name, _, image_tag = image_ref.partition(":")
                img_id = _stable_id(image_name, NodeType.CONTAINER_IMAGE)
                nodes.append(
                    Node(
                        id=img_id,
                        name=image_name,
                        component_type=NodeType.CONTAINER_IMAGE,
                        confidence=0.9,
                        metadata=NodeMetadata(
                            image_name=image_name,
                            image_tag=image_tag or "latest",
                        ),
                        evidence=[
                            _ev(
                                f"Container image '{image_ref}' in {kind} '{name}'",
                                file_path,
                            )
                        ],
                    )
                )

        if runs_as_root:
            findings.append("container_runs_as_root")
        if not has_health_check:
            findings.append("missing_health_check")
        if not has_resource_limits:
            findings.append("no_resource_limits")

        dep_id = _stable_id(name, NodeType.DEPLOYMENT)
        nodes.append(
            Node(
                id=dep_id,
                name=name,
                component_type=NodeType.DEPLOYMENT,
                confidence=0.9,
                metadata=NodeMetadata(
                    deployment_target="kubernetes",
                    runs_as_root=runs_as_root or None,
                    has_health_check=has_health_check if not has_health_check else None,
                    has_resource_limits=has_resource_limits if not has_resource_limits else None,
                ),
                evidence=[_ev(f"Kubernetes {kind} '{name}'", file_path)],
            )
        )

        return nodes, findings


def _extract_k8s_permissions(doc: dict) -> list[str]:
    """Extract permission strings from a Role or ClusterRole."""
    perms: list[str] = []
    rules = doc.get("rules") or []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        api_groups = rule.get("apiGroups", [""])
        resources = rule.get("resources", [])
        verbs = rule.get("verbs", [])
        for group in api_groups:
            for resource in resources:
                for verb in verbs:
                    perms.append(f"{group}/{resource}:{verb}")
    return perms
