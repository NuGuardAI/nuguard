"""Markdown export plugin.

Renders the SBOM as a human-readable Markdown report, suitable for
inclusion in pull requests, wikis, and security reviews.

Output sections
---------------
- Header with target name, generation timestamp, and schema version
- Summary table (node/dep counts, data classification, frameworks, …)
- AI Components table (name, component_type, confidence, details)
- Per-type detail sections (prompts, datastores, models, agents, etc.)
- Dependencies table (name, version, group, license)
- Node Type Breakdown (from summary.node_counts, if present)
"""

from __future__ import annotations

import logging
from typing import Any

from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.markdown")


# ── Markdown helpers ──────────────────────────────────────────────────────────


def _esc(val: Any) -> str:
    """Escape pipe characters so they don't break Markdown table cells."""
    return str(val).replace("|", "\\|")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    sep_row = "| " + " | ".join("---" for _ in headers) + " |"
    data_rows = ["| " + " | ".join(_esc(c) for c in row) + " |" for row in rows]
    return "\n".join([header_row, sep_row] + data_rows)


def _meta(node: dict[str, Any]) -> dict[str, Any]:
    return node.get("metadata") or {}


def _extras(node: dict[str, Any]) -> dict[str, Any]:
    return _meta(node).get("extras") or {}


def _node_detail_summary(node: dict[str, Any]) -> str:
    """One-line detail string for the AI Components table, based on component type."""
    meta = _meta(node)
    extras = _extras(node)
    ctype = node.get("component_type", "")
    parts: list[str] = []

    if ctype == "PROMPT":
        content = extras.get("content", "")
        if content:
            snippet = content[:80].replace("\n", " ")
            parts.append(f'"{snippet}{"…" if len(content) > 80 else ""}"')
        role = extras.get("role")
        if role:
            parts.append(f"role={role}")
    elif ctype == "DATASTORE":
        ds = meta.get("datastore_type")
        if ds:
            parts.append(ds)
        dc = meta.get("data_classification") or []
        if dc:
            parts.append(", ".join(dc))
    elif ctype == "MODEL":
        provider = extras.get("provider")
        if provider:
            parts.append(provider)
        model_name = meta.get("model_name")
        if model_name:
            parts.append(model_name)
    elif ctype == "AGENT":
        fw = meta.get("framework")
        if fw:
            parts.append(fw)
    elif ctype == "TOOL":
        fw = meta.get("framework")
        if fw:
            parts.append(fw)
    elif ctype == "API_ENDPOINT":
        ep = meta.get("endpoint")
        method = meta.get("method")
        transport = meta.get("transport")
        if method and ep:
            parts.append(f"{method} {ep}")
        elif ep:
            parts.append(ep)
        if transport:
            parts.append(transport)
    elif ctype == "AUTH":
        at = meta.get("auth_type")
        if at:
            parts.append(at)
    elif ctype == "CONTAINER_IMAGE":
        parts.append(meta.get("base_image") or "")
    elif ctype == "PRIVILEGE":
        ps = meta.get("privilege_scope")
        if ps:
            parts.append(ps)
    elif ctype == "IAM":
        it = meta.get("iam_type")
        if it:
            parts.append(it)
        perms = meta.get("permissions") or []
        if perms:
            parts.append(f"{len(perms)} permission(s)")
    elif ctype == "DEPLOYMENT":
        dt = meta.get("deployment_target")
        if dt:
            parts.append(dt)
    elif ctype == "GUARDRAIL":
        fw = meta.get("framework")
        if fw:
            parts.append(fw)
    elif ctype == "FRAMEWORK":
        fw = meta.get("framework")
        if fw:
            parts.append(fw)

    return " · ".join(p for p in parts if p)


def _prompt_details(nodes: list[dict[str, Any]]) -> list[str]:
    """Render a Prompt Details section with content snippets."""
    prompts = [n for n in nodes if n.get("component_type") == "PROMPT"]
    if not prompts:
        return []
    lines: list[str] = ["### Prompt Details", ""]
    for n in prompts:
        extras = _extras(n)
        content = extras.get("content", "")
        role = extras.get("role", "")
        is_template = extras.get("is_template", False)
        template_vars = extras.get("template_variables") or []
        injection_risk = extras.get("injection_risk_score")

        lines.append(f"**{_esc(n.get('name', ''))}**")
        if role:
            lines.append(f"- Role: `{role}`")
        if content:
            snippet = content[:300].replace("\n", " ")
            if len(content) > 300:
                snippet += "…"
            lines.append(f"- Content: {_esc(snippet)}")
        if is_template:
            lines.append(f"- Template variables: {', '.join(f'`{v}`' for v in template_vars)}")
        if injection_risk is not None and injection_risk > 0:
            lines.append(f"- Injection risk score: {injection_risk:.1f}")
        lines.append("")
    return lines


def _datastore_details(nodes: list[dict[str, Any]]) -> list[str]:
    """Render a Datastore Details section with classification info."""
    datastores = [n for n in nodes if n.get("component_type") == "DATASTORE"]
    if not datastores:
        return []
    lines: list[str] = ["### Datastore Details", ""]
    for n in datastores:
        meta = _meta(n)
        ds_type = meta.get("datastore_type", "")
        dc = meta.get("data_classification") or []
        tables = meta.get("classified_tables") or []
        fields = meta.get("classified_fields") or {}

        header = _esc(n.get("name", ""))
        if ds_type:
            header += f" ({ds_type})"
        lines.append(f"**{header}**")
        if dc:
            lines.append(f"- Classification: **{', '.join(dc)}**")
        if tables:
            lines.append(f"- Classified tables: {', '.join(f'`{t}`' for t in tables)}")
        if fields:
            lines.append("- Sensitive fields:")
            for tbl, flds in fields.items():
                lines.append(f"  - `{tbl}`: {', '.join(f'`{f}`' for f in flds)}")
        lines.append("")
    return lines


def _model_details(nodes: list[dict[str, Any]]) -> list[str]:
    """Render a Model Details section with provider/API info."""
    models = [n for n in nodes if n.get("component_type") == "MODEL"]
    if not models:
        return []
    lines: list[str] = ["### Model Details", ""]
    rows: list[list[Any]] = []
    for n in models:
        extras = _extras(n)
        rows.append(
            [
                n.get("name", ""),
                extras.get("provider", ""),
                extras.get("model_family", ""),
                extras.get("api_endpoint", ""),
            ]
        )
    lines += [_table(["Model", "Provider", "Family", "API Endpoint"], rows), ""]
    return lines


def _container_image_details(nodes: list[dict[str, Any]]) -> list[str]:
    """Render Container Image details."""
    images = [n for n in nodes if n.get("component_type") == "CONTAINER_IMAGE"]
    if not images:
        return []
    lines: list[str] = ["### Container Images", ""]
    rows: list[list[Any]] = []
    for n in images:
        meta = _meta(n)
        extras = _extras(n)
        findings = extras.get("security_findings") or []
        rows.append(
            [
                meta.get("base_image", ""),
                meta.get("registry", ""),
                "Yes" if extras.get("multi_stage_build") else "No",
                ", ".join(findings) if findings else "—",
            ]
        )
    lines += [_table(["Image", "Registry", "Multi-stage", "Security Findings"], rows), ""]
    return lines


def _privilege_details(nodes: list[dict[str, Any]]) -> list[str]:
    """Render Privilege details."""
    privs = [n for n in nodes if n.get("component_type") == "PRIVILEGE"]
    if not privs:
        return []
    lines: list[str] = ["### Privileges", ""]
    for n in privs:
        meta = _meta(n)
        scope = meta.get("privilege_scope", "")
        lines.append(f"- **{_esc(n.get('name', ''))}**: scope=`{scope}`")
    lines.append("")
    return lines


def _iam_details(nodes: list[dict[str, Any]]) -> list[str]:
    """Render IAM entity details."""
    iam_nodes = [n for n in nodes if n.get("component_type") == "IAM"]
    if not iam_nodes:
        return []
    lines: list[str] = ["### IAM Entities", ""]
    for n in iam_nodes:
        meta = _meta(n)
        iam_type = meta.get("iam_type", "")
        principal = meta.get("principal", "")
        perms = meta.get("permissions") or []
        lines.append(f"**{_esc(n.get('name', ''))}**")
        if iam_type:
            lines.append(f"- Type: `{iam_type}`")
        if principal:
            lines.append(f"- Principal: `{principal}`")
        if perms:
            lines.append(f"- Permissions: {', '.join(f'`{p}`' for p in perms[:20])}")
        lines.append("")
    return lines


# ── Plugin ────────────────────────────────────────────────────────────────────


class MarkdownExporterPlugin(ToolPlugin):
    name = "markdown_export"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        target = sbom.get("target") or "unknown"
        generated = sbom.get("generated_at") or ""
        schema_ver = sbom.get("schema_version") or ""
        nodes = sbom.get("nodes") or []
        deps = sbom.get("deps") or []
        summary = sbom.get("summary") or {}

        _log.info(
            "generating Markdown report for '%s' (%d node(s), %d dep(s))",
            target,
            len(nodes),
            len(deps),
        )

        lines: list[str] = []

        # ── Header ──────────────────────────────────────────────────────────
        lines += [f"# SBOM Report: {target}", ""]
        if generated:
            lines += [f"**Generated:** {generated}  "]
        if schema_ver:
            lines += [f"**Schema version:** {schema_ver}  "]
        lines += [""]

        # ── Summary ─────────────────────────────────────────────────────────
        lines += ["## Summary", ""]
        summary_rows: list[list[Any]] = [
            ["AI nodes", len(nodes)],
            ["Dependencies", len(deps)],
        ]
        dc = summary.get("data_classification") or []
        if dc:
            summary_rows.append(["Data classification", ", ".join(dc)])
        classified_tables = summary.get("classified_tables") or []
        if classified_tables:
            summary_rows.append(["Classified tables", ", ".join(classified_tables)])
        use_case = summary.get("use_case")
        if use_case:
            summary_rows.append(["Use case", use_case])
        frameworks = summary.get("frameworks") or []
        if frameworks:
            summary_rows.append(["Frameworks", ", ".join(frameworks)])
        modalities = summary.get("modalities") or []
        if modalities:
            summary_rows.append(["Modalities", ", ".join(modalities)])
        lines += [_table(["Field", "Value"], summary_rows), ""]

        # ── AI Components ────────────────────────────────────────────────────
        if nodes:
            lines += ["## AI Components", ""]
            node_rows: list[list[Any]] = [
                [
                    n.get("name", ""),
                    n.get("component_type", ""),
                    f"{n['confidence']:.0%}" if isinstance(n.get("confidence"), float) else "",
                    _node_detail_summary(n),
                ]
                for n in nodes
            ]
            lines += [_table(["Name", "Type", "Confidence", "Details"], node_rows), ""]

            # ── Per-type detail sub-sections ─────────────────────────────────
            lines += _prompt_details(nodes)
            lines += _datastore_details(nodes)
            lines += _model_details(nodes)
            lines += _container_image_details(nodes)
            lines += _privilege_details(nodes)
            lines += _iam_details(nodes)

        # ── Dependencies ─────────────────────────────────────────────────────
        if deps:
            lines += ["## Dependencies", ""]
            dep_rows: list[list[Any]] = [
                [
                    d.get("name", ""),
                    d.get("version_spec") or d.get("version") or "",
                    d.get("group", ""),
                    d.get("license", ""),
                ]
                for d in deps
            ]
            lines += [_table(["Name", "Version", "Group", "License"], dep_rows), ""]

        # ── Node Type Breakdown ───────────────────────────────────────────────
        node_counts: dict[str, Any] = summary.get("node_counts") or {}
        if node_counts:
            lines += ["## Node Type Breakdown", ""]
            count_rows: list[list[Any]] = [[k, v] for k, v in sorted(node_counts.items())]
            lines += [_table(["Type", "Count"], count_rows), ""]

        markdown = "\n".join(lines)
        _log.debug("generated %d character(s) of Markdown", len(markdown))

        return ToolResult(
            status="ok",
            tool=self.name,
            message=f"Markdown report generated ({len(nodes)} node(s), {len(deps)} dep(s))",
            details={"markdown": markdown},
        )
