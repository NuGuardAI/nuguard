"""GitHub Actions workflow scanner.

Parses .github/workflows/*.yml files to extract DEPLOYMENT and IAM nodes,
and detect hardcoded secrets.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from nuguard.models.sbom import (
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)

_SECRET_ENV_PATTERN = re.compile(
    r"(SECRET|TOKEN|KEY|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE
)

_SENSITIVE_PERMS = frozenset({"contents: write", "id-token: write", "packages: write"})


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


class GitHubActionsScanner:
    """Scan a GitHub Actions workflow YAML and extract graph nodes."""

    def scan(self, file_path: Path) -> tuple[list[Node], list[str]]:
        """Return (nodes, security_findings) for *file_path*.

        Args:
            file_path: Path to a .github/workflows/*.yml file.

        Returns:
            Tuple of nodes and security finding strings.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return [], ["pyyaml_not_installed"]

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], []

        try:
            data = yaml.safe_load(source)
        except Exception:
            return [], []

        if not isinstance(data, dict):
            return [], []

        nodes: list[Node] = []
        findings: list[str] = []

        workflow_name = data.get("name") or file_path.stem
        dep_id = _stable_id(workflow_name, NodeType.DEPLOYMENT)

        # Collect step annotations
        uses_annotations: list[str] = []
        jobs = data.get("jobs", {}) or {}
        for job_name, job in (jobs.items() if isinstance(jobs, dict) else []):
            if not isinstance(job, dict):
                continue
            steps = job.get("steps", []) or []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses", "")
                if uses:
                    uses_annotations.append(str(uses))
                # Check env blocks for secrets
                env = step.get("env", {}) or {}
                if isinstance(env, dict):
                    for key, val in env.items():
                        if val is not None and not str(val).startswith("${{"):
                            if _SECRET_ENV_PATTERN.search(str(key)):
                                findings.append("secrets_in_env_vars")

        # Also check top-level env
        top_env = data.get("env", {}) or {}
        if isinstance(top_env, dict):
            for key, val in top_env.items():
                if val is not None and not str(val).startswith("${{"):
                    if _SECRET_ENV_PATTERN.search(str(key)):
                        findings.append("secrets_in_env_vars")

        extras: dict = {}
        if uses_annotations:
            extras["uses"] = uses_annotations[:10]  # cap for brevity

        nodes.append(
            Node(
                id=dep_id,
                name=workflow_name,
                component_type=NodeType.DEPLOYMENT,
                confidence=0.8,
                metadata=NodeMetadata(
                    deployment_target="github_actions",
                    extras=extras,
                ),
                evidence=[_ev(f"GitHub Actions workflow '{workflow_name}'", file_path)],
            )
        )

        # Permissions block → IAM node
        perms = data.get("permissions")
        if perms:
            sensitive = self._check_sensitive_perms(perms)
            perm_strings = self._flatten_perms(perms)
            iam_name = f"{workflow_name}-permissions"
            iam_id = _stable_id(iam_name, NodeType.IAM)
            nodes.append(
                Node(
                    id=iam_id,
                    name=iam_name,
                    component_type=NodeType.IAM,
                    confidence=0.75,
                    metadata=NodeMetadata(
                        iam_type="github_actions_permissions",
                        permissions=perm_strings,
                        extras={"sensitive": sensitive},
                    ),
                    evidence=[_ev(f"GitHub Actions permissions for '{workflow_name}'", file_path)],
                )
            )

        return nodes, list(dict.fromkeys(findings))

    @staticmethod
    def _check_sensitive_perms(perms: object) -> bool:
        if isinstance(perms, str) and perms in ("write-all", "write"):
            return True
        if isinstance(perms, dict):
            for key, val in perms.items():
                if str(val) == "write" and key in ("contents", "id-token", "packages"):
                    return True
        return False

    @staticmethod
    def _flatten_perms(perms: object) -> list[str]:
        if isinstance(perms, str):
            return [perms]
        if isinstance(perms, dict):
            return [f"{k}: {v}" for k, v in perms.items()]
        return []
