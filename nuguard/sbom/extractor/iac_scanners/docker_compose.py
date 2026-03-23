"""Docker Compose IaC scanner for nuguard SBOM extraction.

Scans docker-compose.yml files to detect container images and security findings.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import yaml

from nuguard.sbom.models import Node, NodeMetadata, NodeType

logger = logging.getLogger(__name__)

_CONFIDENCE = 0.90

# Environment variable name patterns that indicate secrets
_SECRET_ENV_PATTERNS = {"PASSWORD", "SECRET", "KEY", "TOKEN"}


def _stable_id(key: str) -> uuid.UUID:
    """Generate a stable UUID5 from a canonical key string."""
    return uuid.uuid5(uuid.NAMESPACE_URL, key)


def _parse_image(image_str: str) -> tuple[str, str | None]:
    """Parse 'nginx:1.24' → ('nginx', '1.24'). Returns (name, tag)."""
    if ":" in image_str:
        parts = image_str.split(":", 1)
        return parts[0], parts[1]
    return image_str, None


def _has_secret_env(env: Any) -> bool:
    """Return True if any environment variable name contains a secret pattern."""
    if isinstance(env, dict):
        for key in env:
            key_upper = str(key).upper()
            if any(pat in key_upper for pat in _SECRET_ENV_PATTERNS):
                return True
    elif isinstance(env, list):
        for item in env:
            if isinstance(item, str):
                # "KEY=value" or just "KEY"
                var_name = item.split("=", 1)[0].upper()
                if any(pat in var_name for pat in _SECRET_ENV_PATTERNS):
                    return True
    return False


class DockerComposeScanner:
    """Scans docker-compose YAML files for container images and security findings."""

    def scan(self, path: Path) -> tuple[list[Node], list[str]]:
        """Scan the docker-compose file at *path*.

        Returns (nodes, finding_ids) where finding_ids is a deduplicated list.
        """
        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
        except Exception as exc:
            logger.warning("DockerComposeScanner: failed to read %s: %s", path, exc)
            return [], []

        if not isinstance(data, dict):
            return [], []

        services = data.get("services", {})
        if not isinstance(services, dict):
            return [], []

        nodes: list[Node] = []
        findings_set: set[str] = set()

        for service_name, service_cfg in services.items():
            if not isinstance(service_cfg, dict):
                continue

            # Determine image name and tag
            image_val = service_cfg.get("image")
            build_val = service_cfg.get("build")

            if image_val:
                image_name, image_tag = _parse_image(str(image_val))
                display_name = image_name
                key = f"docker-compose:image:{path}:{image_name}"
            elif build_val is not None:
                # build: . or build: context/path
                image_name = service_name
                image_tag = None
                display_name = service_name
                key = f"docker-compose:build:{path}:{service_name}"
            else:
                continue

            # Collect metadata flags
            has_health_check = "healthcheck" in service_cfg
            has_resource_limits = _check_resource_limits(service_cfg)
            runs_as_root = service_cfg.get("user") == "root"

            node_id = _stable_id(key)
            node = Node(
                id=node_id,
                name=display_name,
                component_type=NodeType.CONTAINER_IMAGE,
                confidence=_CONFIDENCE,
                metadata=NodeMetadata(
                    framework="docker-compose",
                    image_name=display_name,
                    image_tag=image_tag,
                    runs_as_root=runs_as_root,
                    has_health_check=has_health_check,
                    has_resource_limits=has_resource_limits,
                ),
            )
            nodes.append(node)

            # Generate findings
            if runs_as_root:
                findings_set.add("container_runs_as_root")
            if not has_health_check:
                findings_set.add("missing_health_check")
            if not has_resource_limits:
                findings_set.add("no_resource_limits")

            # Check environment variables for secrets
            env = service_cfg.get("environment")
            if env and _has_secret_env(env):
                findings_set.add("secrets_in_env_vars")

        return nodes, list(findings_set)


def _check_resource_limits(service_cfg: dict) -> bool:
    """Return True if the service has resource limits defined."""
    deploy = service_cfg.get("deploy", {})
    if not isinstance(deploy, dict):
        return False
    resources = deploy.get("resources", {})
    if not isinstance(resources, dict):
        return False
    limits = resources.get("limits")
    return bool(limits)
