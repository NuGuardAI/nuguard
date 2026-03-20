"""GraphStore abstraction — networkx v1, Neo4j v2.

TODO: Implement NetworkXGraphStore and the GraphStore abstract base class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class GraphStore(ABC):
    """Abstract interface for attack graph storage."""

    @abstractmethod
    def add_node(self, node_id: str, **attrs) -> None: ...

    @abstractmethod
    def add_edge(self, source: str, target: str, **attrs) -> None: ...

    @abstractmethod
    def get_node(self, node_id: str) -> dict: ...

    @abstractmethod
    def neighbors(self, node_id: str) -> list[str]: ...


class NetworkXGraphStore(GraphStore):
    """In-process networkx-backed graph store.

    TODO: Implement full GraphStore interface using networkx.DiGraph.
    """

    def __init__(self) -> None:
        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("networkx is required for NetworkXGraphStore") from exc
        self._g = nx.DiGraph()

    def add_node(self, node_id: str, **attrs) -> None:
        self._g.add_node(node_id, **attrs)

    def add_edge(self, source: str, target: str, **attrs) -> None:
        self._g.add_edge(source, target, **attrs)

    def get_node(self, node_id: str) -> dict:
        return dict(self._g.nodes.get(node_id, {}))

    def neighbors(self, node_id: str) -> list[str]:
        return list(self._g.successors(node_id))
