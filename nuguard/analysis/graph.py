"""Dict-based SBOM graph index for analysis plugins.

``AnalysisGraph`` provides O(1) adjacency lookups against the plain ``dict``
representation produced by ``AiSbomDocument.model_dump()``.  It deliberately
does **not** depend on Pydantic models or the behavior module, keeping the
analysis package self-contained.

Usage::

    g = AnalysisGraph(sbom_dict)
    for agent in g.nodes_of_type("AGENT"):
        tools = g.targets(agent["id"], "CALLS")
        if g.has_protection(agent["id"]):
            ...
"""
from __future__ import annotations

from typing import Any


class AnalysisGraph:
    """Indexed view of a plain SBOM dict.

    Indexes are built once in ``__init__`` (O(n + e)) and all query methods
    run in O(1) amortised time (O(d) for BFS where d = discovered nodes).
    """

    def __init__(self, sbom: dict[str, Any]) -> None:
        nodes: list[dict[str, Any]] = list(sbom.get("nodes") or [])
        edges: list[dict[str, Any]] = list(sbom.get("edges") or [])

        # O(1) UUID lookup; normalise to str because model_dump() may emit UUID objects
        self._node_by_id: dict[str, dict[str, Any]] = {
            str(n.get("id", "")): n for n in nodes
        }

        # O(1) type-based access
        self._by_type: dict[str, list[dict[str, Any]]] = {}
        for n in nodes:
            ct = (n.get("component_type") or "UNKNOWN").upper()
            self._by_type.setdefault(ct, []).append(n)

        # O(1) outgoing edges: src_id → {rel_type → [edge, ...]}
        self._out: dict[str, dict[str, list[dict[str, Any]]]] = {}
        # O(1) incoming edges: tgt_id → {rel_type → [edge, ...]}
        self._in: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for e in edges:
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            rel = (e.get("relationship_type") or "").upper()
            if src and tgt and rel:
                self._out.setdefault(src, {}).setdefault(rel, []).append(e)
                self._in.setdefault(tgt, {}).setdefault(rel, []).append(e)

    # ── Basic lookups ──────────────────────────────────────────────────────────

    def nodes_of_type(self, component_type: str) -> list[dict[str, Any]]:
        """All nodes with the given component_type (case-insensitive)."""
        return list(self._by_type.get(component_type.upper(), []))

    def node_by_id(self, node_id: str) -> dict[str, Any] | None:
        """Return node dict for the given ID, or None if not found."""
        return self._node_by_id.get(str(node_id))

    def targets(self, node_id: str, rel_type: str) -> list[dict[str, Any]]:
        """Nodes that *node_id* points to via *rel_type* edges."""
        result = []
        for e in self._out.get(str(node_id), {}).get(rel_type.upper(), []):
            n = self._node_by_id.get(str(e.get("target", "")))
            if n is not None:
                result.append(n)
        return result

    def sources(self, node_id: str, rel_type: str) -> list[dict[str, Any]]:
        """Nodes that point *to* node_id via *rel_type* edges."""
        result = []
        for e in self._in.get(str(node_id), {}).get(rel_type.upper(), []):
            n = self._node_by_id.get(str(e.get("source", "")))
            if n is not None:
                result.append(n)
        return result

    def edges_from(self, node_id: str, rel_type: str) -> list[dict[str, Any]]:
        """All edge dicts from *node_id* with the given relationship type."""
        return list(self._out.get(str(node_id), {}).get(rel_type.upper(), []))

    def edges_to(self, node_id: str, rel_type: str) -> list[dict[str, Any]]:
        """All edge dicts pointing to *node_id* with the given relationship type."""
        return list(self._in.get(str(node_id), {}).get(rel_type.upper(), []))

    # ── Traversal helpers ──────────────────────────────────────────────────────

    def reachable_of_type(
        self,
        start_id: str,
        rel_types: list[str],
        target_type: str,
        max_depth: int = 6,
    ) -> list[dict[str, Any]]:
        """BFS from *start_id* following *rel_types*; returns nodes of *target_type*."""
        rel_set = {r.upper() for r in rel_types}
        tgt_ct = target_type.upper()
        visited: set[str] = {str(start_id)}
        frontier: set[str] = {str(start_id)}
        found: list[dict[str, Any]] = []
        for _ in range(max_depth):
            if not frontier:
                break
            next_f: set[str] = set()
            for nid in frontier:
                for rel in rel_set:
                    for e in self._out.get(nid, {}).get(rel, []):
                        tid = str(e.get("target", ""))
                        if not tid or tid in visited:
                            continue
                        visited.add(tid)
                        next_f.add(tid)
                        n = self._node_by_id.get(tid)
                        if n is not None and (n.get("component_type") or "").upper() == tgt_ct:
                            found.append(n)
            frontier = next_f
        return found

    def accesses_paths(
        self, agent_id: str, _seen: frozenset[str] | None = None
    ) -> list[tuple[dict[str, Any] | None, dict[str, Any], str | None]]:
        """Return ``(intermediary, datastore, access_type)`` tuples from *agent_id*.

        Covers three shapes:
        - Direct:      AGENT → ACCESSES → DATASTORE
        - Via tool:    AGENT → CALLS → TOOL → ACCESSES → DATASTORE
        - Delegated:   AGENT → DELEGATES_TO → AGENT → [CALLS →] TOOL → ACCESSES → DATASTORE

        The *intermediary* is the TOOL node for indirect paths, ``None`` for direct.
        Recursion depth is bounded by the visited set ``_seen``.
        """
        _seen = _seen or frozenset()
        agent_id = str(agent_id)
        if agent_id in _seen:
            return []
        _seen = _seen | {agent_id}

        seen_pairs: set[tuple[str | None, str]] = set()
        results: list[tuple[dict[str, Any] | None, dict[str, Any], str | None]] = []

        def _add(
            interm: dict[str, Any] | None,
            ds: dict[str, Any],
            access_type: str | None,
        ) -> None:
            key = (str(interm["id"]) if interm else None, str(ds["id"]))
            if key not in seen_pairs:
                seen_pairs.add(key)
                results.append((interm, ds, access_type))

        # Direct: AGENT → ACCESSES → DATASTORE
        for e in self.edges_from(agent_id, "ACCESSES"):
            tgt = self._node_by_id.get(str(e.get("target", "")))
            if tgt and (tgt.get("component_type") or "").upper() == "DATASTORE":
                _add(None, tgt, e.get("access_type"))

        # Via tool: AGENT → CALLS → TOOL → ACCESSES → DATASTORE
        for tool in self.targets(agent_id, "CALLS"):
            for e in self.edges_from(str(tool["id"]), "ACCESSES"):
                tgt = self._node_by_id.get(str(e.get("target", "")))
                if tgt and (tgt.get("component_type") or "").upper() == "DATASTORE":
                    _add(tool, tgt, e.get("access_type"))

        # Via delegation: AGENT → DELEGATES_TO → AGENT → ...
        for delegate in self.targets(agent_id, "DELEGATES_TO"):
            for interm, ds, at in self.accesses_paths(str(delegate["id"]), _seen=_seen):
                # Use delegate as intermediary when the recursive path has no tool
                _add(interm if interm is not None else delegate, ds, at)

        return results

    # ── Semantic helpers ───────────────────────────────────────────────────────

    def has_protection(self, node_id: str) -> bool:
        """True if a GUARDRAIL or AUTH node has a PROTECTS edge pointing at *node_id*."""
        protected_types = {"GUARDRAIL", "AUTH"}
        for rel_type in ("PROTECTS",):
            for e in self.edges_to(str(node_id), rel_type):
                src = self._node_by_id.get(str(e.get("source", "")))
                if src and (src.get("component_type") or "").upper() in protected_types:
                    return True
        return False

    def write_agents_for(self, datastore_id: str) -> list[dict[str, Any]]:
        """Return all AGENT nodes that have a write or readwrite access path to *datastore_id*.

        Covers both direct and tool-mediated paths (via ``accesses_paths``).
        """
        ds_id = str(datastore_id)
        result: list[dict[str, Any]] = []
        for agent in self.nodes_of_type("AGENT"):
            for _, ds, access_type in self.accesses_paths(str(agent["id"])):
                if str(ds.get("id", "")) == ds_id:
                    if (access_type or "").lower() in ("write", "readwrite"):
                        result.append(agent)
                        break
        return result
