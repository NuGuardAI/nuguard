"""Render an :class:`~nuguard.graph.models.AttackGraph` as LLM-facing text.

Deterministic and attribute-complete — no LLM call involved. Contrast with
:mod:`nuguard.sbom.core.relationship_graph`, which renders a stripped-down
Mermaid diagram plus an LLM-written narrative for human docs.
"""
from __future__ import annotations

from .models import AttackGraph

_MAX_STR_LEN = 400


def _format_value(value: object) -> str:
    if isinstance(value, str):
        text = value.replace("\n", " ").strip()
        if len(text) > _MAX_STR_LEN:
            text = text[:_MAX_STR_LEN] + "…"
        return text
    if isinstance(value, (list, tuple)):
        return ", ".join(_format_value(v) for v in value) if value else "[]"
    if isinstance(value, dict):
        return ", ".join(f"{k}={_format_value(v)}" for k, v in value.items()) if value else "{}"
    return str(value)


def render_graph_text(graph: AttackGraph) -> str:
    """Render the full graph as structured Markdown for an LLM's context window.

    Nodes are grouped by component type and list every carried attribute;
    relationships follow as a flat edge list keyed by node slug.
    """
    lines: list[str] = [
        f"# Target Application Graph: {graph.target}",
        "",
        f"{len(graph.nodes)} components, {len(graph.edges)} relationships.",
        "",
        "## Components",
        "",
    ]

    by_type: dict[str, list] = {}
    for node in graph.nodes:
        by_type.setdefault(node.type, []).append(node)

    for type_ in sorted(by_type):
        type_nodes = by_type[type_]
        lines.append(f"### {type_} ({len(type_nodes)})")
        lines.append("")
        for node in type_nodes:
            lines.append(f"- **{node.id}** — {node.name} (confidence: {node.confidence:.2f})")
            for key in sorted(node.attributes):
                lines.append(f"    - {key}: {_format_value(node.attributes[key])}")
        lines.append("")

    lines.append("## Relationships")
    lines.append("")
    if graph.edges:
        for edge in graph.edges:
            rel = edge.relationship_type.lower()
            suffix = f" ({edge.access_type})" if edge.access_type else ""
            lines.append(f"- {edge.source} --{rel}{suffix}--> {edge.target}")
    else:
        lines.append("(none)")

    return "\n".join(lines)
