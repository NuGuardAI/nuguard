"""Terraform .tf file scanner.

Uses regex-based HCL parsing (no external hcl2 library required).
Extracts IAM, DEPLOYMENT, and CONTAINER_IMAGE nodes from Terraform files.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from nuguard.models.sbom import (
    Edge,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)

# Match resource blocks: resource "type" "name" {
_RESOURCE_RE = re.compile(
    r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE
)

# Match variable assignments inside blocks
_ATTR_RE = re.compile(r'^\s*(\w+)\s*=\s*"([^"]*)"', re.MULTILINE)

# Match policy ARN attachments
_ADMIN_POLICY_RE = re.compile(
    r"arn:aws:iam::aws:policy/AdministratorAccess", re.IGNORECASE
)

# Match image references in ECS task definitions
_IMAGE_RE = re.compile(r'"image"\s*:\s*"([^"]+)"')

_SECRET_PATTERN = re.compile(
    r"(password|secret|key|token|passwd|pwd|credential)", re.IGNORECASE
)


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _ev(detail: str, path: Path) -> Evidence:
    return Evidence(
        kind=EvidenceKind.IAC,
        confidence=0.8,
        detail=detail,
        location=EvidenceLocation(path=str(path)),
    )


class TerraformScanner:
    """Scan a Terraform .tf file and extract graph nodes."""

    def scan(
        self, file_path: Path
    ) -> tuple[list[Node], list[Edge], list[str]]:
        """Return (nodes, edges, security_findings) for *file_path*.

        Args:
            file_path: Path to a Terraform .tf file.

        Returns:
            Tuple of nodes, edges, and security finding strings.
        """
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], [], []

        nodes: list[Node] = []
        edges: list[Edge] = []
        findings: list[str] = []

        for match in _RESOURCE_RE.finditer(source):
            resource_type = match.group(1)
            resource_name = match.group(2)
            block_start = match.end()
            block_content = self._extract_block(source, block_start)

            if resource_type == "aws_iam_role":
                node = self._make_iam_node(
                    resource_name,
                    "aws_iam_role",
                    block_content,
                    file_path,
                )
                nodes.append(node)

            elif resource_type == "aws_iam_policy":
                node = self._make_iam_node(
                    resource_name,
                    "aws_iam_policy",
                    block_content,
                    file_path,
                )
                nodes.append(node)

            elif resource_type == "aws_iam_role_policy_attachment":
                # Check for AdministratorAccess
                if _ADMIN_POLICY_RE.search(block_content):
                    findings.append("overly_permissive_iam")
                node = self._make_iam_node(
                    resource_name,
                    "aws_iam_role_policy_attachment",
                    block_content,
                    file_path,
                )
                nodes.append(node)

            elif resource_type == "google_service_account":
                principal = self._extract_attr(block_content, "account_id") or resource_name
                nid = _stable_id(resource_name, NodeType.IAM)
                nodes.append(
                    Node(
                        id=nid,
                        name=resource_name,
                        component_type=NodeType.IAM,
                        confidence=0.8,
                        metadata=NodeMetadata(
                            iam_type="gcp_service_account",
                            principal=principal,
                        ),
                        evidence=[_ev(f"GCP service account '{resource_name}'", file_path)],
                    )
                )

            elif resource_type == "azurerm_user_assigned_identity":
                nid = _stable_id(resource_name, NodeType.IAM)
                nodes.append(
                    Node(
                        id=nid,
                        name=resource_name,
                        component_type=NodeType.IAM,
                        confidence=0.8,
                        metadata=NodeMetadata(
                            iam_type="azure_managed_identity",
                            principal=resource_name,
                        ),
                        evidence=[_ev(f"Azure managed identity '{resource_name}'", file_path)],
                    )
                )

            elif resource_type in ("aws_ecs_task_definition", "kubernetes_deployment"):
                dep_id = _stable_id(resource_name, NodeType.DEPLOYMENT)
                nodes.append(
                    Node(
                        id=dep_id,
                        name=resource_name,
                        component_type=NodeType.DEPLOYMENT,
                        confidence=0.8,
                        metadata=NodeMetadata(
                            deployment_target="ecs" if "ecs" in resource_type else "kubernetes",
                        ),
                        evidence=[_ev(f"{resource_type} '{resource_name}'", file_path)],
                    )
                )
                # Extract container images from ECS task definitions
                for img_match in _IMAGE_RE.finditer(block_content):
                    image_ref = img_match.group(1)
                    image_name, _, image_tag = image_ref.partition(":")
                    img_id = _stable_id(image_name, NodeType.CONTAINER_IMAGE)
                    nodes.append(
                        Node(
                            id=img_id,
                            name=image_name,
                            component_type=NodeType.CONTAINER_IMAGE,
                            confidence=0.8,
                            metadata=NodeMetadata(
                                image_name=image_name,
                                image_tag=image_tag or "latest",
                            ),
                            evidence=[_ev(f"Container image '{image_ref}' in {resource_type}", file_path)],
                        )
                    )

        return nodes, edges, list(dict.fromkeys(findings))

    def _make_iam_node(
        self,
        name: str,
        iam_type: str,
        block_content: str,
        file_path: Path,
    ) -> Node:
        principal = self._extract_attr(block_content, "role") or name
        perms = self._extract_wildcard_perms(block_content)
        nid = _stable_id(name, NodeType.IAM)
        return Node(
            id=nid,
            name=name,
            component_type=NodeType.IAM,
            confidence=0.8,
            metadata=NodeMetadata(
                iam_type=iam_type,
                principal=principal,
                permissions=perms,
            ),
            evidence=[_ev(f"{iam_type} '{name}'", file_path)],
        )

    @staticmethod
    def _extract_block(source: str, start: int) -> str:
        """Extract content of the HCL block starting at *start* (after '{')."""
        depth = 1
        i = start
        while i < len(source) and depth > 0:
            c = source[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        return source[start : i - 1]

    @staticmethod
    def _extract_attr(block: str, key: str) -> str | None:
        m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', block, re.MULTILINE)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_wildcard_perms(block: str) -> list[str]:
        """Find any wildcard (*) permission references."""
        perms: list[str] = []
        for m in re.finditer(r'"[^"]*\*[^"]*"', block):
            perms.append(m.group(0).strip('"'))
        return perms
