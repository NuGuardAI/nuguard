"""MarkdownExporterPlugin — generate a human-readable Markdown security report."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from nuguard.models.sbom import AiSbomDocument, NodeType
from nuguard.sbom.toolbox.plugins._base import ToolResult


class MarkdownExporterPlugin:
    """Export SBOM as a developer-readable Markdown security report."""

    def run(self, sbom: AiSbomDocument | dict, config: dict | None = None) -> ToolResult:
        """Render *sbom* as a Markdown report.

        Args:
            sbom: :class:`~nuguard.models.sbom.AiSbomDocument` or plain dict.
            config: Optional ``output_path`` to write Markdown to disk.

        Returns:
            :class:`~nuguard.sbom.toolbox.plugins._base.ToolResult` with
            ``details[0]["markdown"]``.
        """
        config = config or {}
        if isinstance(sbom, dict):
            from nuguard.sbom.extractor.serializer import AiSbomSerializer
            doc = AiSbomSerializer.from_json(sbom)
        else:
            doc = sbom

        # Run vulnerability and atlas plugins for findings
        from nuguard.sbom.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin
        from nuguard.sbom.toolbox.plugins.atlas_annotator import AtlasAnnotatorPlugin

        vuln_result = VulnerabilityScannerPlugin().run(doc, config)
        atlas_result = AtlasAnnotatorPlugin().run(doc, config)

        markdown = self._render(doc, vuln_result.details, atlas_result.details)

        output_path = config.get("output_path")
        if output_path:
            try:
                import pathlib
                pathlib.Path(output_path).write_text(markdown, encoding="utf-8")
            except Exception:
                pass

        return ToolResult(
            status="pass",
            message="Markdown report generated.",
            details=[{"markdown": markdown}],
        )

    def _render(
        self,
        doc: AiSbomDocument,
        vuln_findings: list[dict[str, Any]],
        atlas_annotations: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []

        # Header
        lines.append("# NuGuard AI Security Report")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Target**: {doc.target}")
        lines.append(f"- **Generated**: {doc.generated_at.isoformat()}")
        lines.append(f"- **Schema Version**: {doc.schema_version}")
        if doc.summary.use_case:
            lines.append(f"- **Use Case**: {doc.summary.use_case}")
        lines.append("")
        lines.append("### Node Counts")
        lines.append("")
        if doc.summary.node_counts:
            lines.append("| Type | Count |")
            lines.append("|------|------:|")
            for nt, count in sorted(doc.summary.node_counts.items()):
                lines.append(f"| {nt} | {count} |")
        else:
            lines.append("_No nodes found._")
        lines.append("")

        # Attack Surface
        lines.append("## Attack Surface")
        lines.append("")

        # Agents
        agents = [n for n in doc.nodes if n.component_type == NodeType.AGENT]
        lines.append(f"### Agents ({len(agents)})")
        lines.append("")
        if agents:
            lines.append("| Name | Framework | ID |")
            lines.append("|------|-----------|-----|")
            for a in agents:
                fw = a.metadata.framework or "-"
                lines.append(f"| {a.name} | {fw} | `{a.id}` |")
        else:
            lines.append("_No agents detected._")
        lines.append("")

        # Datastores
        datastores = [n for n in doc.nodes if n.component_type == NodeType.DATASTORE]
        lines.append(f"### Datastores ({len(datastores)})")
        lines.append("")
        if datastores:
            lines.append("| Name | Type | Classification |")
            lines.append("|------|------|----------------|")
            for ds in datastores:
                ds_type = ds.metadata.datastore_type.value if ds.metadata.datastore_type else "-"
                classifications = ", ".join(dc.value for dc in ds.metadata.data_classification) or "-"
                lines.append(f"| {ds.name} | {ds_type} | {classifications} |")
        else:
            lines.append("_No datastores detected._")
        lines.append("")

        # API Endpoints
        endpoints = [n for n in doc.nodes if n.component_type == NodeType.API_ENDPOINT]
        if endpoints:
            lines.append(f"### API Endpoints ({len(endpoints)})")
            lines.append("")
            lines.append("| Name | Method | Path | Auth |")
            lines.append("|------|--------|------|------|")
            for ep in endpoints:
                method = ep.metadata.method or "-"
                path = ep.metadata.endpoint or "-"
                auth = ep.metadata.auth_type or "none"
                lines.append(f"| {ep.name} | {method} | {path} | {auth} |")
            lines.append("")

        # Security Findings
        lines.append("## Security Findings")
        lines.append("")
        if vuln_findings:
            by_severity: dict[str, list[dict]] = defaultdict(list)
            for f in vuln_findings:
                by_severity[f.get("severity", "medium")].append(f)

            for sev in ("critical", "high", "medium", "low"):
                sev_findings = by_severity.get(sev, [])
                if not sev_findings:
                    continue
                lines.append(f"### {sev.capitalize()} ({len(sev_findings)})")
                lines.append("")
                for f in sev_findings:
                    lines.append(f"- **[{f.get('rule_id', '?')}]** {f.get('description', '')}")
                    if f.get("remediation"):
                        lines.append(f"  - *Remediation*: {f['remediation']}")
                lines.append("")
        else:
            lines.append("_No security findings._")
            lines.append("")

        # MITRE ATLAS Annotations
        lines.append("## MITRE ATLAS Annotations")
        lines.append("")
        if atlas_annotations:
            lines.append("| Technique ID | Technique Name | Confidence |")
            lines.append("|-------------|----------------|-----------|")
            for ann in atlas_annotations:
                tid = ann.get("technique_id", "-")
                tname = ann.get("technique_name", "-")
                conf = f"{ann.get('confidence', 0):.2f}"
                lines.append(f"| {tid} | {tname} | {conf} |")
            lines.append("")
            for ann in atlas_annotations:
                lines.append(f"### {ann.get('technique_id')} — {ann.get('technique_name')}")
                lines.append("")
                lines.append(f"{ann.get('description', '')}")
                nodes = ann.get("affected_nodes", [])
                if nodes:
                    lines.append(f"- **Affected nodes**: {', '.join(f'`{n}`' for n in nodes)}")
                lines.append("")
        else:
            lines.append("_No MITRE ATLAS annotations._")
            lines.append("")

        # Dependencies
        if doc.deps:
            lines.append(f"## Dependencies ({len(doc.deps)})")
            lines.append("")
            lines.append("| Package | Version | Ecosystem |")
            lines.append("|---------|---------|-----------|")
            for dep in doc.deps[:20]:  # cap display at 20
                ver = dep.version_spec or "-"
                eco = dep.ecosystem or "-"
                lines.append(f"| {dep.name} | {ver} | {eco} |")
            if len(doc.deps) > 20:
                lines.append(f"| _...{len(doc.deps) - 20} more_ | | |")
            lines.append("")

        lines.append("---")
        lines.append("_Generated by [NuGuard](https://github.com/NuGuardAI/nuguard-oss)_")

        return "\n".join(lines)
