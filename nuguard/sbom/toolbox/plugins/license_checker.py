"""License policy checker plugin.

Enforces a caller-supplied deny-list of SPDX license identifiers across
all SBOM sources:

  * ``nodes`` — checked via ``metadata.extras.license``
  * ``deps``  — checked via the top-level ``license`` field

Any component whose license appears in the deny list is recorded as a
violation.  The plugin makes no network requests; all checks are pure
in-memory comparisons against the policy.

Config keys
-----------
  deny    list of SPDX identifiers to reject  (default: [])
          e.g. ["GPL-3.0", "AGPL-3.0", "LGPL-2.1"]

Status semantics
----------------
  failed   one or more license violations found
  ok       no violations
"""
from __future__ import annotations

import logging
from typing import Any

from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.license")


class LicenseCheckerPlugin(ToolPlugin):
    name = "license_check"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        deny  = set(config.get("deny", []))
        nodes = sbom.get("nodes") or []
        deps  = sbom.get("deps") or []
        _log.debug("checking %d node(s) and %d dep(s) against %d denied license(s)",
                   len(nodes), len(deps), len(deny))

        violations: list[dict[str, Any]] = []

        for node in nodes:
            license_name = node.get("metadata", {}).get("extras", {}).get("license")
            if license_name and license_name in deny:
                violations.append({"source": "node", "name": node.get("name"), "license": license_name})

        for dep in deps:
            license_name = dep.get("license")
            if license_name and license_name in deny:
                violations.append({"source": "dep", "name": dep.get("name"), "license": license_name})

        if violations:
            _log.warning("%d license violation(s) found", len(violations))
        else:
            _log.debug("no license violations")
        status  = "failed" if violations else "ok"
        message = "License policy violations found" if violations else "License policy check passed"
        return ToolResult(
            status=status,
            tool=self.name,
            message=message,
            details={
                "violations":    violations,
                "nodes_checked": len(nodes),
                "deps_checked":  len(deps),
            },
        )
