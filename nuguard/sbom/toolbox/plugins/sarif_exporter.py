"""SARIF 2.1.0 export plugin.

Runs the built-in vulnerability scanner and converts its findings to
SARIF (Static Analysis Results Interchange Format) 2.1.0, consumable
by GitHub Code Scanning, VS Code SARIF Viewer, and other SARIF-aware tools.

Severity → SARIF level mapping
-------------------------------
  CRITICAL / HIGH  → error
  MEDIUM           → warning
  LOW / INFO / *   → note

Config keys
-----------
  provider       vela-rules | osv | grype | all  (default: vela-rules)
  timeout        network timeout in seconds       (default: 15.0)
  grype_timeout  grype subprocess timeout         (default: 60.0)
  artifact_uri   URI for the scanned artifact     (default: sbom target or "sbom.json")
"""
from __future__ import annotations

import logging
from typing import Any

from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.sarif")

_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
    "Schemata/sarif-schema-2.1.0.json"
)
_TOOL_NAME     = "xelo-toolbox"
_TOOL_VERSION  = "0.1.2"
_TOOL_INFO_URI = "https://nuguard.ai"

_SEV_TO_LEVEL: dict[str, str] = {
    "CRITICAL": "error",
    "HIGH":     "error",
    "MEDIUM":   "warning",
    "LOW":      "note",
    "INFO":     "note",
    "UNKNOWN":  "note",
}


# ── SARIF builder helpers ─────────────────────────────────────────────────────

def _make_rule(finding: dict[str, Any]) -> dict[str, Any]:
    rule_id = finding["rule_id"]
    title   = finding.get("title", rule_id)
    desc    = finding.get("description", "")
    return {
        "id":               rule_id,
        "name":             rule_id,
        "shortDescription": {"text": title},
        "fullDescription":  {"text": desc},
        "helpUri":          finding.get("advisory_url") or _TOOL_INFO_URI,
        "properties": {
            "tags":     ["security"],
            "severity": finding.get("severity", "UNKNOWN"),
        },
    }


def _make_result(finding: dict[str, Any], artifact_uri: str) -> dict[str, Any]:
    level    = _SEV_TO_LEVEL.get(finding.get("severity", "UNKNOWN"), "note")
    affected = finding.get("affected") or []
    msg      = finding.get("description", finding.get("title", finding["rule_id"]))
    if affected:
        msg += f" Affected: {', '.join(str(a) for a in affected)}."

    sarif_result: dict[str, Any] = {
        "ruleId": finding["rule_id"],
        "level":  level,
        "message": {"text": msg},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri":       artifact_uri,
                        "uriBaseId": "%SRCROOT%",
                    }
                }
            }
        ],
    }
    if finding.get("remediation"):
        sarif_result["fixes"] = [{"description": {"text": finding["remediation"]}}]
    return sarif_result


# ── Plugin ────────────────────────────────────────────────────────────────────

class SarifExporterPlugin(ToolPlugin):
    name = "sarif_export"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        from xelo.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin

        artifact_uri = (
            config.get("artifact_uri")
            or sbom.get("target")
            or "sbom.json"
        )
        vuln_config: dict[str, Any] = {
            "provider":      config.get("provider", "vela-rules"),
            "timeout":       float(config.get("timeout", 15.0)),
            "grype_timeout": float(config.get("grype_timeout", 60.0)),
        }

        _log.info(
            "running vuln scan for SARIF export (provider=%s)", vuln_config["provider"]
        )
        vuln_result = VulnerabilityScannerPlugin().run(sbom, vuln_config)
        findings: list[dict[str, Any]] = vuln_result.details.get("findings") or []
        _log.info("building SARIF 2.1.0 document from %d finding(s)", len(findings))

        # Deduplicate rules by rule_id (preserve first-seen order)
        seen_rule_ids: set[str] = set()
        rules: list[dict[str, Any]] = []
        for f in findings:
            rid = f["rule_id"]
            if rid not in seen_rule_ids:
                seen_rule_ids.add(rid)
                rules.append(_make_rule(f))

        results = [_make_result(f, artifact_uri) for f in findings]

        sarif: dict[str, Any] = {
            "$schema": _SARIF_SCHEMA,
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name":           _TOOL_NAME,
                            "version":        _TOOL_VERSION,
                            "informationUri": _TOOL_INFO_URI,
                            "rules":          rules,
                        }
                    },
                    "results": results,
                }
            ],
        }

        return ToolResult(
            status=vuln_result.status,
            tool=self.name,
            message=f"SARIF 2.1.0 export generated with {len(findings)} finding(s)",
            details=sarif,
        )
