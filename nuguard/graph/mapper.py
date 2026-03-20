"""Map an AiSbomDocument to attack graph nodes and edges.

TODO: Implement XeloSBOM → Node/Edge list translation.
"""

from __future__ import annotations

from nuguard.models.sbom import AiSbomDocument


def map_sbom_to_graph(doc: AiSbomDocument) -> tuple[list, list]:
    """Translate *doc* into (attack_nodes, attack_edges).

    TODO: Implement full mapping logic. Filter non-attack nodes, enrich with
    risk attributes.
    """
    raise NotImplementedError("map_sbom_to_graph not yet implemented")
