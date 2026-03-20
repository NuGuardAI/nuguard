"""Serialize and deserialize attack graphs to/from JSON (Postgres JSONB).

TODO: Implement networkx.DiGraph ↔ JSON round-trip.
"""

from __future__ import annotations


def graph_to_json(graph) -> dict:
    """Serialize *graph* to a JSON-serializable dict.

    TODO: Implement serialisation.
    """
    raise NotImplementedError("graph_to_json not yet implemented")


def json_to_graph(data: dict):
    """Deserialize a dict produced by :func:`graph_to_json` back to a graph.

    TODO: Implement deserialisation.
    """
    raise NotImplementedError("json_to_graph not yet implemented")
