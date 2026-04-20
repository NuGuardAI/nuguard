"""Relationship graph renderer for AI-SBOM documents.

Produces a Markdown section containing:
  1. A Mermaid flowchart diagram of the key component relationships.
  2. An LLM-written narrative that explains the graph in plain English.

Only AGENT, TOOL, MODEL, PROMPT, GUARDRAIL, DATASTORE, FRAMEWORK, and
API_ENDPOINT nodes are included — IaC/deployment/auth noise is filtered out
to keep the diagram focused on the AI system's logical structure.

The Mermaid ``flowchart LR`` format renders in GitHub, VS Code (Markdown
Preview Enhanced), Notion, GitLab, and most modern doc platforms.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)

# Component types included in the relationship diagram
_GRAPH_TYPES = {
    "AGENT",
    "TOOL",
    "MODEL",
    "PROMPT",
    "GUARDRAIL",
    "DATASTORE",
    "FRAMEWORK",
    "API_ENDPOINT",
}

# Mermaid node shape per component type
_SHAPE: dict[str, tuple[str, str]] = {
    # (open_bracket, close_bracket)
    "AGENT":        ("[", "]"),       # rectangle
    "TOOL":         ("(", ")"),       # rounded rectangle
    "MODEL":        ("[(", ")]"),     # cylinder
    "PROMPT":       (">", "]"),       # asymmetric / flag
    "GUARDRAIL":    ("{", "}"),       # diamond
    "DATASTORE":    ("[(", ")]"),     # cylinder
    "FRAMEWORK":    ("[/", "/]"),     # parallelogram
    "API_ENDPOINT": ("([", "])"),     # stadium
}

# Edge label per relationship type
_EDGE_LABEL: dict[str, str] = {
    "CALLS":      "calls",
    "USES":       "uses",
    "ACCESSES":   "accesses",
    "PROTECTS":   "protects",
    "INVOKES":    "invokes",
    "DEPENDS_ON": "depends on",
    "ROUTES_TO":  "routes to",
}

_MERMAID_ID_RE = re.compile(r"[^A-Za-z0-9_]")


def _mermaid_id(name: str, node_id: str) -> str:
    """Return a safe Mermaid node identifier (alphanumeric + underscore)."""
    safe = _MERMAID_ID_RE.sub("_", name)
    # Prefix with first 6 chars of UUID to guarantee uniqueness
    return f"{safe}_{node_id[:6]}"


def _mermaid_label(name: str, component_type: str) -> str:
    """Return a concise display label for the Mermaid node."""
    emoji = {
        "AGENT":        "🤖",
        "TOOL":         "🔧",
        "MODEL":        "🧠",
        "PROMPT":       "💬",
        "GUARDRAIL":    "🛡",
        "DATASTORE":    "🗄",
        "FRAMEWORK":    "⚙",
        "API_ENDPOINT": "🌐",
    }.get(component_type, "")
    label = name[:40].replace('"', "'")
    return f"{emoji} {label}" if emoji else label


def build_mermaid_graph(doc: "AiSbomDocument") -> str:
    """Build a Mermaid flowchart from the SBOM edges and nodes.

    Returns the raw Mermaid source (no fences) or an empty string when there
    are no relevant nodes.
    """
    # Index nodes by ID, filter to graph-relevant types only
    node_by_id: dict[str, Any] = {}
    for node in doc.nodes:
        ct = str(
            node.component_type.value
            if hasattr(node.component_type, "value")
            else node.component_type
        )
        if ct in _GRAPH_TYPES:
            node_by_id[str(node.id)] = node

    if not node_by_id:
        return ""

    lines: list[str] = ["flowchart LR"]

    # Node definitions
    for node_id, node in node_by_id.items():
        ct = str(
            node.component_type.value
            if hasattr(node.component_type, "value")
            else node.component_type
        )
        mid = _mermaid_id(node.name, node_id)
        label = _mermaid_label(node.name, ct)
        open_b, close_b = _SHAPE.get(ct, ("[", "]"))
        lines.append(f'    {mid}{open_b}"{label}"{close_b}')

    lines.append("")

    # Edge definitions — only between nodes in the graph
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in doc.edges:
        src_id = str(edge.source)
        tgt_id = str(edge.target)
        rel = str(
            edge.relationship_type.value
            if hasattr(edge.relationship_type, "value")
            else edge.relationship_type
        )
        if src_id not in node_by_id or tgt_id not in node_by_id:
            continue
        key = (src_id, tgt_id, rel)
        if key in seen_edges:
            continue
        seen_edges.add(key)

        src_node = node_by_id[src_id]
        tgt_node = node_by_id[tgt_id]
        src_mid = _mermaid_id(src_node.name, src_id)
        tgt_mid = _mermaid_id(tgt_node.name, tgt_id)
        edge_label = _EDGE_LABEL.get(rel, rel.lower())
        lines.append(f"    {src_mid} -->|{edge_label}| {tgt_mid}")

    # Style classes
    lines.extend([
        "",
        "    classDef agent    fill:#4A90D9,stroke:#2C6FAC,color:#fff",
        "    classDef tool     fill:#7ED321,stroke:#5A9A18,color:#fff",
        "    classDef model    fill:#9B59B6,stroke:#7D3C98,color:#fff",
        "    classDef prompt   fill:#F39C12,stroke:#D68910,color:#fff",
        "    classDef guard    fill:#E74C3C,stroke:#C0392B,color:#fff",
        "    classDef store    fill:#1ABC9C,stroke:#17A589,color:#fff",
        "    classDef fw       fill:#95A5A6,stroke:#717D7E,color:#fff",
        "    classDef endpoint fill:#3498DB,stroke:#2471A3,color:#fff",
    ])

    # Apply classes
    type_to_class = {
        "AGENT": "agent", "TOOL": "tool", "MODEL": "model",
        "PROMPT": "prompt", "GUARDRAIL": "guard", "DATASTORE": "store",
        "FRAMEWORK": "fw", "API_ENDPOINT": "endpoint",
    }
    for ct, cls in type_to_class.items():
        ids = [
            _mermaid_id(n.name, str(n.id))
            for n in node_by_id.values()
            if str(
                n.component_type.value
                if hasattr(n.component_type, "value")
                else n.component_type
            ) == ct
        ]
        if ids:
            lines.append(f"    class {','.join(ids)} {cls}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM narrative
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a security architect writing a concise, plain-English explanation "
    "of an AI system's component relationships for a technical audience. "
    "Focus on the data flow: which agents orchestrate which tools, what data "
    "stores are reachable, what guardrails are in place, and any notable risk "
    "patterns (e.g. unguarded tools, external data access). "
    "Be factual — only describe what the graph shows. "
    "Use Markdown with bullet points. Keep it under 200 words."
)


def _graph_context(doc: "AiSbomDocument") -> str:
    """Build a compact text representation of the graph for the LLM prompt."""
    lines: list[str] = []
    node_by_id: dict[str, Any] = {str(n.id): n for n in doc.nodes}

    for edge in doc.edges:
        src = node_by_id.get(str(edge.source))
        tgt = node_by_id.get(str(edge.target))
        if src is None or tgt is None:
            continue
        src_ct = str(
            src.component_type.value
            if hasattr(src.component_type, "value")
            else src.component_type
        )
        tgt_ct = str(
            tgt.component_type.value
            if hasattr(tgt.component_type, "value")
            else tgt.component_type
        )
        if src_ct not in _GRAPH_TYPES and tgt_ct not in _GRAPH_TYPES:
            continue
        rel = str(
            edge.relationship_type.value
            if hasattr(edge.relationship_type, "value")
            else edge.relationship_type
        )
        lines.append(f"{src.name} ({src_ct}) --[{rel}]--> {tgt.name} ({tgt_ct})")

    # Include isolated nodes (no edges)
    nodes_in_edges: set[str] = set()
    for e in doc.edges:
        nodes_in_edges.add(str(e.source))
        nodes_in_edges.add(str(e.target))
    for node in doc.nodes:
        nid = str(node.id)
        if nid not in nodes_in_edges:
            ct = str(
                node.component_type.value
                if hasattr(node.component_type, "value")
                else node.component_type
            )
            if ct in _GRAPH_TYPES:
                lines.append(f"{node.name} ({ct}) [no connections]")

    return "\n".join(lines) if lines else "No relationships detected."


async def build_relationship_graph_with_llm(
    doc: "AiSbomDocument",
    llm_client: Any,
) -> str:
    """Build the full Markdown relationship graph section.

    Returns a Markdown string containing:
    - An LLM-written narrative paragraph
    - A Mermaid code block with the flowchart

    Falls back to the diagram-only section on LLM errors.
    """
    mermaid_src = build_mermaid_graph(doc)
    if not mermaid_src:
        return ""

    mermaid_block = f"```mermaid\n{mermaid_src}\n```"

    # Try LLM narrative
    context = _graph_context(doc)
    user_prompt = (
        f"Here are the AI system component relationships:\n\n{context}\n\n"
        "Write a concise plain-English explanation of this system's architecture, "
        "data flows, and any notable security observations (e.g. unguarded tools, "
        "sensitive datastore access, missing guardrail coverage). "
        "Format as Markdown bullet points."
    )

    narrative = ""
    try:
        text, _ = await llm_client.complete_text(system=_SYSTEM, user=user_prompt)
        narrative = text.strip()
    except Exception as exc:
        _log.warning("relationship-graph: LLM narrative failed — diagram only: %s", exc)

    parts: list[str] = ["## Component Relationship Graph\n"]
    if narrative:
        parts.append(narrative)
        parts.append("")
    parts.append(mermaid_block)

    return "\n".join(parts)
