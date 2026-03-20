"""Docker Compose YAML scanner.

Parses docker-compose.yml / docker-compose.yaml files to extract
CONTAINER_IMAGE nodes and security findings.
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
    r"(PASSWORD|SECRET|KEY|TOKEN|PASSWD|CREDENTIAL)", re.IGNORECASE
)


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _ev(detail: str, path: Path) -> Evidence:
    return Evidence(
        kind=EvidenceKind.IAC,
        confidence=0.85,
        detail=detail,
        location=EvidenceLocation(path=str(path)),
    )


class DockerComposeScanner:
    """Scan a docker-compose YAML and extract container image nodes."""

    def scan(self, file_path: Path) -> tuple[list[Node], list[str]]:
        """Return (nodes, security_findings) for *file_path*.

        Args:
            file_path: Path to a docker-compose.yml / docker-compose.yaml.

        Returns:
            Tuple of CONTAINER_IMAGE nodes and security finding strings.
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

        services = data.get("services", {}) or {}
        nodes: list[Node] = []
        findings: list[str] = []

        for service_name, service_config in services.items():
            if not isinstance(service_config, dict):
                continue

            image_ref: str | None = None
            image_name: str = service_name
            image_tag: str = "local"
            is_local_build = False

            # image: name:tag
            if "image" in service_config:
                image_ref = str(service_config["image"])
                image_name, _, tag = image_ref.partition(":")
                image_tag = tag or "latest"

            # build: . or build: context: ...
            elif "build" in service_config:
                is_local_build = True
                image_name = service_name

            # runs_as_root check
            user_val = service_config.get("user")
            runs_as_root: bool | None = None
            if user_val is not None:
                runs_as_root = str(user_val).strip() in ("root", "0", "0:0")

            # healthcheck check
            has_health_check: bool | None = None
            if "healthcheck" in service_config:
                hc = service_config["healthcheck"]
                if isinstance(hc, dict) and hc.get("disable"):
                    has_health_check = False
                else:
                    has_health_check = True
            else:
                has_health_check = False

            # resource limits
            has_resource_limits: bool | None = None
            deploy = service_config.get("deploy", {}) or {}
            if isinstance(deploy, dict):
                resources = deploy.get("resources", {}) or {}
                if isinstance(resources, dict) and resources.get("limits"):
                    has_resource_limits = True
                else:
                    has_resource_limits = False
            else:
                has_resource_limits = False

            # Environment variable secrets check
            env = service_config.get("environment", {})
            if isinstance(env, list):
                for item in env:
                    if isinstance(item, str):
                        key_part = item.split("=")[0]
                        val_part = item.split("=", 1)[1] if "=" in item else None
                        if val_part and _SECRET_ENV_PATTERN.search(key_part):
                            findings.append("secrets_in_env_vars")
            elif isinstance(env, dict):
                for key, val in env.items():
                    if val is not None and _SECRET_ENV_PATTERN.search(str(key)):
                        findings.append("secrets_in_env_vars")

            # Build findings from metadata
            if runs_as_root:
                findings.append("container_runs_as_root")
            if has_health_check is False:
                findings.append("missing_health_check")
            if has_resource_limits is False:
                findings.append("no_resource_limits")

            nid = _stable_id(image_name, NodeType.CONTAINER_IMAGE)
            detail = (
                f"Local build service '{service_name}'"
                if is_local_build
                else f"Docker Compose service '{service_name}' image: {image_ref}"
            )
            nodes.append(
                Node(
                    id=nid,
                    name=image_name,
                    component_type=NodeType.CONTAINER_IMAGE,
                    confidence=0.9,
                    metadata=NodeMetadata(
                        image_name=image_name,
                        image_tag=image_tag,
                        runs_as_root=runs_as_root,
                        has_health_check=has_health_check,
                        has_resource_limits=has_resource_limits,
                    ),
                    evidence=[_ev(detail, file_path)],
                )
            )

        return nodes, list(dict.fromkeys(findings))
