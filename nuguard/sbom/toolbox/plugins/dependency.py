"""Dependency analyser plugin.

Breaks down the SBOM into two complementary views:

AI component view (``nodes``)
  Groups nodes by ``component_type`` (MODEL, TOOL, AGENT, DATASTORE, …)
  and reports per-type counts alongside the aggregate total.

Package dependency view (``deps``)
  Groups package dependencies by their ``group`` label (ai, general,
  runtime, …) and reports per-group counts alongside the aggregate total.

Where ``summary.node_counts`` is present in the SBOM it is passed through
verbatim, preserving any upstream groupings computed at scan time.

Output keys
-----------
  total_ai_nodes       total number of SBOM nodes
  ai_component_counts  breakdown by component_type
  total_package_deps   total number of package dependencies
  package_dep_groups   breakdown by dep group
  node_counts          summary.node_counts passthrough (may be empty dict)
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.dependency")


class DependencyAnalyzerPlugin(ToolPlugin):
    name = "dependency_analyze"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        nodes = sbom.get("nodes") or []
        deps  = sbom.get("deps") or []
        _log.debug("analysing %d node(s) and %d dep(s)", len(nodes), len(deps))

        # AI component breakdown (nodes only)
        by_type = Counter(node.get("component_type", "UNKNOWN") for node in nodes)

        # Package dependency breakdown by group
        by_group: Counter[str] = Counter()
        for dep in deps:
            group = dep.get("group", "unknown")
            by_group[group] += 1

        # Surface summary counts if available
        summary     = sbom.get("summary") or {}
        node_counts = summary.get("node_counts") or {}

        return ToolResult(
            status="ok",
            tool=self.name,
            message="Dependency analysis complete",
            details={
                "total_ai_nodes":      len(nodes),
                "ai_component_counts": dict(by_type),
                "total_package_deps":  len(deps),
                "package_dep_groups":  dict(by_group),
                "node_counts":         node_counts,
            },
        )
