"""CycloneDX Extended export plugin.

Exports the SBOM as a CycloneDX 1.6 BOM with AI-specific first-class fields:

- ``modelCard`` on ML model components
- ``data.contents`` / ``data.governance`` on datastore components
- ``services`` section for API endpoint nodes
- ``compositions`` section for the full edge graph with relationship types
- ``evidence.identity.occurrences`` for source-file provenance
- ``nuguard:*`` property namespace with risk tags, permissions, access types,
  system-prompt excerpts (redacted), and framework names
"""
from __future__ import annotations

import logging
from typing import Any

from ...models import AiSbomDocument
from ...serializer import AiSbomSerializer
from ..models import ToolResult
from ..plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.cyclonedx_ext")


class CycloneDxExtExporter(ToolPlugin):
    """Export an AiSbomDocument as CycloneDX 1.6 Extended JSON.

    Config keys
    -----------
    spec_version : str   CycloneDX spec version string (default ``"1.6"``)
    """

    name = "cyclonedx_ext_export"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        spec = config.get("spec_version", "1.6")
        _log.info(
            "generating CycloneDX-ext %s BOM (%d node(s))",
            spec,
            len(sbom.get("nodes") or []),
        )
        doc = AiSbomDocument.model_validate(sbom)
        payload = AiSbomSerializer.to_cyclonedx_extended(doc, spec_version=spec)
        n_services = len(payload.get("services", []))
        n_compositions = len(payload.get("compositions", []))
        _log.debug(
            "BOM has %d component(s), %d service(s), %d composition(s)",
            len(payload.get("components", [])),
            n_services,
            n_compositions,
        )
        return ToolResult(
            status="ok",
            tool=self.name,
            message=(
                f"CycloneDX Extended export generated "
                f"({len(payload.get('components', []))} components, "
                f"{n_services} services, {n_compositions} compositions)"
            ),
            details=payload,
        )
