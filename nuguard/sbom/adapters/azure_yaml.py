"""``azure.yaml`` (Azure Developer CLI) adapter — maps azd services to their source projects.

Emits a fact marker per service (``azd_service``: name, project, host, docker context) that
the topology pass joins to the Bicep/Terraform workload carrying the same
``azd-service-name`` tag, giving that cloud workload its ``source_dir``.
"""

from __future__ import annotations

import os
from typing import Any

import yaml

from nuguard.common.logging import get_logger

from ..types import ComponentType
from ..workload import as_dict
from .base import ComponentDetection

_log = get_logger(__name__)


def is_azure_yaml(rel_path: str) -> bool:
    """True for ``azure.yaml`` / ``azure.yml``."""
    return os.path.basename(rel_path).lower() in ("azure.yaml", "azure.yml")


class AzureYamlAdapter:
    """Parses azd service definitions."""

    name = "azure_yaml"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        """One fact marker per azd service; [] when the file is not an azd manifest."""
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            return []
        if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
            return []
        base = os.path.dirname(file_path)
        out: list[ComponentDetection] = []
        for svc_name, svc in data["services"].items():
            if not isinstance(svc, dict) or not svc.get("project"):
                continue
            project = os.path.normpath(os.path.join(base, str(svc["project"]))).replace(os.sep, "/")
            docker = as_dict(svc.get("docker"))
            fact: dict[str, Any] = {
                "name": str(svc_name),
                "project": project,
                "host": str(svc.get("host") or ""),
                "language": str(svc.get("language") or ""),
            }
            if docker.get("path"):
                fact["dockerfile"] = os.path.normpath(
                    os.path.join(project, str(docker["path"]))
                ).replace(os.sep, "/")
            out.append(
                ComponentDetection(
                    component_type=ComponentType.DEPLOYMENT,
                    canonical_name=f"fact:azd:{base or '.'}:{svc_name}".lower(),
                    display_name=f"azd:{svc_name}",
                    adapter_name=self.name,
                    priority=8,
                    confidence=0.85,
                    metadata={"iac_format": "azd", "azd_service": fact, "fact_marker": True},
                    file_path=file_path,
                    line=1,
                    snippet=f"azure.yaml service {svc_name}"[:120],
                    evidence_kind="iac",
                )
            )
        return out
