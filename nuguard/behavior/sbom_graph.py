"""Lightweight behavior-scoped SBOM graph index.

Builds node-id and adjacency indexes once on construction so that graph
traversal in scenario generation and static alignment checks avoids O(n²)
linear scans over sbom.nodes / sbom.edges.

Design mirrors the pattern in nuguard/sbom/enricher.py (88-152) but is kept
separate to avoid coupling behavior logic to the SBOM enrichment pipeline.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument, Edge, Node
    from nuguard.sbom.types import AccessType


class SbomGraph:
    """Indexed view of an AiSbomDocument optimised for behavior traversal.

    All public methods return ``list[Node]`` so callers can iterate without
    knowing internal UUID resolution.  Methods are pure (no mutation of sbom).
    """

    def __init__(self, sbom: "AiSbomDocument") -> None:
        self._sbom = sbom

        # Primary index: UUID → Node
        self._node_by_id: dict[UUID, "Node"] = {n.id: n for n in sbom.nodes}

        # By component type (string value, e.g. "AGENT", "TOOL")
        self._by_type: dict[str, list["Node"]] = {}
        for n in sbom.nodes:
            ct = str(n.component_type.value if hasattr(n.component_type, "value") else n.component_type)
            self._by_type.setdefault(ct, []).append(n)

        # Outgoing edges: source_id → {relationship_type_str → list[Edge]}
        self._outgoing: dict[UUID, dict[str, list["Edge"]]] = {}
        # Incoming edges: target_id → {relationship_type_str → list[Edge]}
        self._incoming: dict[UUID, dict[str, list["Edge"]]] = {}
        for edge in sbom.edges:
            rel = str(
                edge.relationship_type.value
                if hasattr(edge.relationship_type, "value")
                else edge.relationship_type
            )
            self._outgoing.setdefault(edge.source, {}).setdefault(rel, []).append(edge)
            self._incoming.setdefault(edge.target, {}).setdefault(rel, []).append(edge)

    # ------------------------------------------------------------------
    # Basic lookup
    # ------------------------------------------------------------------

    def nodes_of_type(self, component_type: str) -> list["Node"]:
        """Return all nodes with the given component type string (e.g. 'AGENT')."""
        return list(self._by_type.get(component_type.upper(), []))

    def node_by_id(self, node_id: UUID) -> "Node | None":
        """Return the node with the given UUID, or None."""
        return self._node_by_id.get(node_id)

    def targets(self, node_id: UUID, rel_type: str) -> list["Node"]:
        """Return all nodes that *node_id* points to via *rel_type* edges."""
        edges = self._outgoing.get(node_id, {}).get(rel_type.upper(), [])
        result: list["Node"] = []
        for e in edges:
            n = self._node_by_id.get(e.target)
            if n is not None:
                result.append(n)
        return result

    def sources(self, node_id: UUID, rel_type: str) -> list["Node"]:
        """Return all nodes that point *to* node_id via *rel_type* edges."""
        edges = self._incoming.get(node_id, {}).get(rel_type.upper(), [])
        result: list["Node"] = []
        for e in edges:
            n = self._node_by_id.get(e.source)
            if n is not None:
                result.append(n)
        return result

    def edges_from(self, node_id: UUID, rel_type: str) -> list["Edge"]:
        """Return the raw Edge objects outgoing from *node_id* with *rel_type*."""
        return list(self._outgoing.get(node_id, {}).get(rel_type.upper(), []))

    # ------------------------------------------------------------------
    # BFS traversal
    # ------------------------------------------------------------------

    def reachable_of_type(
        self,
        start_id: UUID,
        rel_types: list[str],
        target_type: str,
        max_depth: int = 4,
    ) -> list["Node"]:
        """BFS from *start_id* following only *rel_types* edges, returning
        all reachable nodes whose component_type equals *target_type*.

        Does NOT include the start node itself.
        """
        rel_set = {r.upper() for r in rel_types}
        target_ct = target_type.upper()
        visited: set[UUID] = {start_id}
        frontier: set[UUID] = {start_id}
        found: list["Node"] = []

        for _ in range(max_depth):
            if not frontier:
                break
            next_frontier: set[UUID] = set()
            for nid in frontier:
                for rel in rel_set:
                    for e in self._outgoing.get(nid, {}).get(rel, []):
                        if e.target in visited:
                            continue
                        visited.add(e.target)
                        next_frontier.add(e.target)
                        target_node = self._node_by_id.get(e.target)
                        if target_node is not None:
                            ct = str(
                                target_node.component_type.value
                                if hasattr(target_node.component_type, "value")
                                else target_node.component_type
                            )
                            if ct == target_ct:
                                found.append(target_node)
            frontier = next_frontier

        return found

    # ------------------------------------------------------------------
    # Semantic helpers
    # ------------------------------------------------------------------

    def accesses_paths(
        self,
        agent_id: UUID,
    ) -> list[tuple["Node | None", "Node", "AccessType | None"]]:
        """Return all (intermediary, datastore, access_type) tuples reachable
        from *agent_id*.

        Two path shapes are covered:
        - Direct:     AGENT → ACCESSES → DATASTORE  (intermediary=None)
        - Transitive: AGENT → CALLS → TOOL → ACCESSES → DATASTORE
        - Via delegation: AGENT → DELEGATES_TO → AGENT → [CALLS →] TOOL → ACCESSES → DATASTORE
          (treated as direct lookup one delegation hop deep)

        Returns deduplicated results keyed by (intermediary_id_or_None, datastore_id).
        """
        seen: set[tuple[UUID | None, UUID]] = set()
        results: list[tuple["Node | None", "Node", "AccessType | None"]] = []

        def _add(intermediary: "Node | None", ds_node: "Node", access_type: "AccessType | None") -> None:
            key = (intermediary.id if intermediary else None, ds_node.id)
            if key not in seen:
                seen.add(key)
                results.append((intermediary, ds_node, access_type))

        # 1. Direct AGENT → ACCESSES → DATASTORE
        for edge in self.edges_from(agent_id, "ACCESSES"):
            target = self._node_by_id.get(edge.target)
            if target is not None:
                ct = str(
                    target.component_type.value
                    if hasattr(target.component_type, "value")
                    else target.component_type
                )
                if ct == "DATASTORE":
                    _add(None, target, getattr(edge, "access_type", None))

        # 2. Transitive: AGENT → CALLS → TOOL → ACCESSES → DATASTORE
        for tool_node in self.targets(agent_id, "CALLS"):
            for edge in self.edges_from(tool_node.id, "ACCESSES"):
                ds_node = self._node_by_id.get(edge.target)
                if ds_node is not None:
                    ct = str(
                        ds_node.component_type.value
                        if hasattr(ds_node.component_type, "value")
                        else ds_node.component_type
                    )
                    if ct == "DATASTORE":
                        _add(tool_node, ds_node, getattr(edge, "access_type", None))

        # 3. One DELEGATES_TO hop: AGENT → DELEGATES_TO → AGENT → CALLS → TOOL → ACCESSES
        for delegated_agent in self.targets(agent_id, "DELEGATES_TO"):
            # Direct access by delegated agent
            for edge in self.edges_from(delegated_agent.id, "ACCESSES"):
                ds_node = self._node_by_id.get(edge.target)
                if ds_node is not None:
                    ct = str(
                        ds_node.component_type.value
                        if hasattr(ds_node.component_type, "value")
                        else ds_node.component_type
                    )
                    if ct == "DATASTORE":
                        _add(delegated_agent, ds_node, getattr(edge, "access_type", None))
            # Via tools of delegated agent
            for tool_node in self.targets(delegated_agent.id, "CALLS"):
                for edge in self.edges_from(tool_node.id, "ACCESSES"):
                    ds_node = self._node_by_id.get(edge.target)
                    if ds_node is not None:
                        ct = str(
                            ds_node.component_type.value
                            if hasattr(ds_node.component_type, "value")
                            else ds_node.component_type
                        )
                        if ct == "DATASTORE":
                            _add(tool_node, ds_node, getattr(edge, "access_type", None))

        return results

    def protects_targets(self, guardrail_id: UUID) -> list["Node"]:
        """Return all nodes protected by *guardrail_id* via PROTECTS edges."""
        return self.targets(guardrail_id, "PROTECTS")

    def has_protection(self, node_id: UUID) -> bool:
        """Return True if any GUARDRAIL or AUTH node has a PROTECTS edge to *node_id*."""
        incoming_protects = self._incoming.get(node_id, {}).get("PROTECTS", [])
        for edge in incoming_protects:
            src = self._node_by_id.get(edge.source)
            if src is not None:
                ct = str(
                    src.component_type.value
                    if hasattr(src.component_type, "value")
                    else src.component_type
                )
                if ct in ("GUARDRAIL", "AUTH"):
                    return True
        return False
