"""Build an :class:`~nuguard.graph.models.AttackGraph` from an AI-SBOM document."""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .models import AttackGraph, GraphEdge, GraphNode

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument, Node


def _enum_value(v: object) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _slug(component_type: str, index: int) -> str:
    return f"{component_type.lower()}_{index}"


def _node_attributes(node: "Node") -> dict[str, object]:
    """Every non-default field from the node's metadata, JSON-safe."""
    if node.metadata is None:
        return {}
    return node.metadata.model_dump(exclude_none=True, exclude_defaults=True, mode="json")


def build_attack_graph(sbom: "AiSbomDocument") -> AttackGraph:
    """Convert an :class:`AiSbomDocument` into a traversable, fully-attributed :class:`AttackGraph`.

    Every SBOM node becomes a :class:`~nuguard.graph.models.GraphNode` addressed by a
    short stable slug (``agent_1``, ``tool_3``, ...) instead of its raw UUID, carrying
    every populated metadata field (system prompt excerpts, guardrail rules, privilege
    scopes, auth posture, PII fields, and so on). Every SBOM edge becomes a
    :class:`~nuguard.graph.models.GraphEdge` between two slugs. Edges referencing a
    node not present in ``sbom.nodes`` are dropped.
    """
    counters: dict[str, int] = defaultdict(int)
    slug_by_uuid: dict[str, str] = {}
    nodes: list[GraphNode] = []

    for node in sbom.nodes:
        component_type = _enum_value(node.component_type)
        counters[component_type] += 1
        slug = _slug(component_type, counters[component_type])
        slug_by_uuid[str(node.id)] = slug
        nodes.append(
            GraphNode(
                id=slug,
                sbom_id=str(node.id),
                name=node.name,
                type=component_type,
                confidence=node.confidence,
                attributes=_node_attributes(node),
            )
        )

    edges: list[GraphEdge] = []
    for edge in sbom.edges:
        source_slug = slug_by_uuid.get(str(edge.source))
        target_slug = slug_by_uuid.get(str(edge.target))
        if source_slug is None or target_slug is None:
            continue
        edges.append(
            GraphEdge(
                source=source_slug,
                target=target_slug,
                relationship_type=_enum_value(edge.relationship_type),
                access_type=_enum_value(edge.access_type) if edge.access_type is not None else None,
            )
        )

    return AttackGraph(target=sbom.target, nodes=nodes, edges=edges)
