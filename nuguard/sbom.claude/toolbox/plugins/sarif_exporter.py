"""SarifExporterPlugin — convert VLA findings to SARIF 2.1.0."""

from __future__ import annotations

import json
from typing import Any

from nuguard.models.sbom import AiSbomDocument
from nuguard.sbom.toolbox.plugins._base import ToolResult
from nuguard.sbom.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin

_SARIF_SCHEMA = "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0.json"
_NUGUARD_VERSION = "0.1.0"

_LEVEL_MAP = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
}


class SarifExporterPlugin:
    """Export SBOM toolbox findings as SARIF 2.1.0."""

    def run(self, sbom: AiSbomDocument | dict, config: dict | None = None) -> ToolResult:
        """Export findings from *sbom* to SARIF format.

        Runs VulnerabilityScannerPlugin internally, then converts findings to
        SARIF 2.1.0.  The SARIF dict is returned in ``details[0]["sarif"]``.

        Args:
            sbom: :class:`~nuguard.models.sbom.AiSbomDocument` or plain dict.
            config: Optional ``output_path`` to write SARIF JSON to disk.

        Returns:
            :class:`~nuguard.sbom.toolbox.plugins._base.ToolResult` with SARIF
            in ``details``.
        """
        config = config or {}
        if isinstance(sbom, dict):
            from nuguard.sbom.extractor.serializer import AiSbomSerializer
            doc = AiSbomSerializer.from_json(sbom)
        else:
            doc = sbom

        # Run vulnerability scanner to get findings
        vuln_plugin = VulnerabilityScannerPlugin()
        vuln_result = vuln_plugin.run(doc, config)

        sarif = self._build_sarif(vuln_result.details, doc)

        # Optionally write to file
        output_path = config.get("output_path")
        if output_path:
            try:
                import pathlib
                pathlib.Path(output_path).write_text(
                    json.dumps(sarif, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

        return ToolResult(
            status=vuln_result.status,
            message=f"SARIF export complete. {vuln_result.message}",
            details=[{"sarif": sarif}],
        )

    def _build_sarif(
        self, findings: list[dict[str, Any]], doc: AiSbomDocument
    ) -> dict[str, Any]:
        rules: list[dict] = []
        results: list[dict] = []
        seen_rules: set[str] = set()

        for finding in findings:
            rule_id = finding.get("rule_id", "UNKNOWN")
            severity = finding.get("severity", "medium")
            description = finding.get("description", "")
            affected_nodes = finding.get("affected_nodes", [])
            remediation = finding.get("remediation", "")

            if rule_id not in seen_rules:
                seen_rules.add(rule_id)
                rules.append(
                    {
                        "id": rule_id,
                        "name": rule_id,
                        "shortDescription": {"text": description[:100]},
                        "fullDescription": {"text": description},
                        "help": {"text": remediation},
                        "properties": {"severity": severity},
                    }
                )

            # Build locations from affected nodes
            locations = self._build_locations(affected_nodes, doc)

            results.append(
                {
                    "ruleId": rule_id,
                    "level": _LEVEL_MAP.get(severity, "warning"),
                    "message": {"text": description},
                    "locations": locations,
                    "properties": {
                        "severity": severity,
                        "affected_nodes": affected_nodes,
                        "remediation": remediation,
                    },
                }
            )

        sarif: dict[str, Any] = {
            "version": "2.1.0",
            "$schema": _SARIF_SCHEMA,
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "nuguard",
                            "version": _NUGUARD_VERSION,
                            "informationUri": "https://github.com/NuGuardAI/nuguard-oss",
                            "rules": rules,
                        }
                    },
                    "results": results,
                    "artifacts": [
                        {
                            "location": {"uri": doc.target},
                            "description": {"text": f"AI-SBOM for {doc.target}"},
                        }
                    ],
                }
            ],
        }
        return sarif

    def _build_locations(
        self, node_ids: list[str], doc: AiSbomDocument
    ) -> list[dict]:
        locations: list[dict] = []
        node_map = {n.id: n for n in doc.nodes}
        for nid in node_ids[:3]:  # cap at 3 locations per result
            node = node_map.get(nid)
            if not node:
                continue
            # Get file path from evidence if available
            ev_path = None
            if node.evidence:
                ev = node.evidence[0]
                if ev.location:
                    ev_path = ev.location.path

            uri = ev_path or doc.target
            locations.append(
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri},
                        "region": {
                            "startLine": (
                                node.evidence[0].location.line
                                if node.evidence and node.evidence[0].location and node.evidence[0].location.line
                                else 1
                            )
                        },
                    },
                    "logicalLocations": [
                        {
                            "name": node.name,
                            "kind": node.component_type.value,
                        }
                    ],
                }
            )
        return locations or [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": doc.target},
                    "region": {"startLine": 1},
                }
            }
        ]
