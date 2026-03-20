"""JFrog Xray SBOM submission plugin.

Packages the Vela SBOM as a JSON payload and POSTs it to the JFrog Xray
REST API (``POST /api/v1/sbom``).  Xray indexes the submission and applies
its own vulnerability and license policies against the package list.

The payload includes:
  projectKey   the Xray project that owns the scanning policies
  format       "JSON"
  sbom         the raw Vela SBOM document
  metadata     tenant_id, application_id, source, tool

Config keys
-----------
  url             Xray base URL                              (required)
  project         Xray project key                          (required)
  token           bearer token                              (required)
  tenant_id       tenant identifier                         (required)
  application_id  application identifier                    (required)
  timeout         HTTP timeout in seconds                   (default: 10.0)
  retries         retry attempts on transient failure       (default: 2)
"""
from __future__ import annotations

import logging
from typing import Any

from xelo.toolbox.integration_contracts import XrayConfig
from xelo.toolbox.http_utils import post_json
from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.xray")


class XrayPlugin(ToolPlugin):
    name = "xray_submit"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        cfg = XrayConfig.model_validate(config)
        base_url = str(cfg.url).rstrip("/")

        endpoint = f"{base_url}/api/v1/sbom"
        _log.info("submitting SBOM to JFrog Xray: %s (project=%s)", base_url, cfg.project)
        payload = self._build_payload(sbom=sbom, cfg=cfg)
        response = post_json(
            url=endpoint,
            payload=payload,
            headers={"Authorization": f"Bearer {cfg.token}"},
            timeout=cfg.timeout,
            retries=cfg.retries,
        )
        _log.debug("xray response: %s", response)
        return ToolResult(
            status="ok",
            tool=self.name,
            message="SBOM submitted to JFrog Xray",
            details={"request_summary": payload["metadata"], "response": response},
        )

    @staticmethod
    def _build_payload(sbom: dict[str, Any], cfg: XrayConfig) -> dict[str, Any]:
        return {
            "projectKey": cfg.project,
            "format": "JSON",
            "sbom": sbom,
            "metadata": {
                "tenant_id":     cfg.tenant_id,
                "application_id": cfg.application_id,
                "source":        "xelo-toolbox",
                "tool":          "xray_submit",
            },
        }
