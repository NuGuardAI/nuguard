"""Data model for the target-application attack graph.

:class:`AttackGraph` is a slug-addressed, fully-attributed, traversable view
of an AI-SBOM, meant to be handed to (or queried by) an LLM-driven red-team
agent during recon. Unlike the Mermaid diagram in
:mod:`nuguard.sbom.core.relationship_graph` (which drops most node types and
carries no attributes, for human docs), every node here keeps every
non-default field its SBOM metadata carries — system prompt excerpts,
guardrail rules, privilege scopes, auth posture, PII fields, and so on.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr


class GraphNode(BaseModel):
    """One AI-SBOM component, addressed by a short stable slug."""

    id: str = Field(description="Short stable slug, e.g. 'agent_1' — used in edges and traversal calls")
    sbom_id: str = Field(description="Original SBOM node UUID, for cross-referencing findings/reports")
    name: str
    type: str = Field(description="Component type, e.g. 'AGENT', 'TOOL', 'GUARDRAIL', 'DATASTORE'")
    confidence: float
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Every non-default field from the SBOM node's metadata (system_prompt_excerpt, "
        "rules_excerpt, blocked_topics, privilege_scope, pii_fields, auth_required, ...)",
    )


class GraphEdge(BaseModel):
    """A directed relationship between two :class:`GraphNode` slugs."""

    source: str
    target: str
    relationship_type: str = Field(description="e.g. 'CALLS', 'ACCESSES', 'PROTECTS', 'USES', 'DELEGATES_TO'")
    access_type: str | None = Field(default=None, description="For ACCESSES edges: 'read' | 'write' | 'readwrite'")


Direction = Literal["out", "in", "both"]


class AttackGraph(BaseModel):
    """A traversable graph of a target application's components and relationships."""

    target: str = Field(description="Repository URL or local path the source SBOM was generated from")
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    _by_id: dict[str, GraphNode] = PrivateAttr(default_factory=dict)
    _by_type: dict[str, list[GraphNode]] = PrivateAttr(default_factory=dict)
    _outgoing: dict[str, list[GraphEdge]] = PrivateAttr(default_factory=dict)
    _incoming: dict[str, list[GraphEdge]] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        self._by_id = {n.id: n for n in self.nodes}
        self._by_type = {}
        for n in self.nodes:
            self._by_type.setdefault(n.type, []).append(n)
        self._outgoing = {}
        self._incoming = {}
        for e in self.edges:
            self._outgoing.setdefault(e.source, []).append(e)
            self._incoming.setdefault(e.target, []).append(e)

    # ── lookup ────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> GraphNode | None:
        """Return the node with this slug, or ``None`` if it doesn't exist."""
        return self._by_id.get(node_id)

    def nodes_by_type(self, type_: str) -> list[GraphNode]:
        """Return all nodes of the given component type (e.g. ``'AGENT'``)."""
        return list(self._by_type.get(type_, []))

    # ── local traversal ──────────────────────────────────────────────────

    def outgoing(self, node_id: str) -> list[GraphEdge]:
        """Edges where ``node_id`` is the source."""
        return list(self._outgoing.get(node_id, []))

    def incoming(self, node_id: str) -> list[GraphEdge]:
        """Edges where ``node_id`` is the target."""
        return list(self._incoming.get(node_id, []))

    def neighbors(self, node_id: str, direction: Direction = "out") -> list[GraphNode]:
        """Nodes directly connected to ``node_id``.

        ``direction`` selects outgoing edges, incoming edges, or both.
        """
        edges: list[GraphEdge] = []
        if direction in ("out", "both"):
            edges.extend(self._outgoing.get(node_id, []))
        if direction in ("in", "both"):
            edges.extend(self._incoming.get(node_id, []))
        seen: list[GraphNode] = []
        seen_ids: set[str] = set()
        for e in edges:
            other_id = e.target if e.source == node_id else e.source
            if other_id in seen_ids:
                continue
            node = self._by_id.get(other_id)
            if node is not None:
                seen_ids.add(other_id)
                seen.append(node)
        return seen

    # ── multi-hop traversal ──────────────────────────────────────────────

    def reachable_from(self, node_id: str, max_depth: int | None = None) -> list[GraphNode]:
        """Breadth-first set of nodes reachable from ``node_id`` following outgoing edges.

        ``node_id`` itself is not included. ``max_depth`` caps the number of
        hops (``None`` = unbounded).
        """
        if node_id not in self._by_id:
            return []
        visited: set[str] = {node_id}
        result: list[GraphNode] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if max_depth is not None and depth >= max_depth:
                continue
            for e in self._outgoing.get(current, []):
                if e.target in visited:
                    continue
                visited.add(e.target)
                node = self._by_id.get(e.target)
                if node is not None:
                    result.append(node)
                    queue.append((e.target, depth + 1))
        return result

    def shortest_path(self, from_id: str, to_id: str) -> list[GraphNode] | None:
        """Shortest hop path from ``from_id`` to ``to_id`` (inclusive), or ``None`` if unreachable."""
        if from_id not in self._by_id or to_id not in self._by_id:
            return None
        if from_id == to_id:
            node = self._by_id[from_id]
            return [node]
        visited: set[str] = {from_id}
        parent: dict[str, str] = {}
        queue: deque[str] = deque([from_id])
        while queue:
            current = queue.popleft()
            for e in self._outgoing.get(current, []):
                if e.target in visited:
                    continue
                visited.add(e.target)
                parent[e.target] = current
                if e.target == to_id:
                    path_ids = [to_id]
                    while path_ids[-1] != from_id:
                        path_ids.append(parent[path_ids[-1]])
                    path_ids.reverse()
                    return [self._by_id[i] for i in path_ids]
                queue.append(e.target)
        return None

    # ── recon convenience ────────────────────────────────────────────────

    def entry_agents(self) -> list[GraphNode]:
        """AGENT nodes with no incoming CALLS/DELEGATES_TO from another AGENT.

        A heuristic for "where a user's message first lands" in a
        (possibly multi-agent) system.
        """
        agent_ids = {n.id for n in self._by_type.get("AGENT", [])}
        has_agent_caller: set[str] = set()
        for n_id in agent_ids:
            for e in self._incoming.get(n_id, []):
                if e.relationship_type in ("CALLS", "DELEGATES_TO") and e.source in agent_ids:
                    has_agent_caller.add(n_id)
        return [n for n in self._by_type.get("AGENT", []) if n.id not in has_agent_caller]
