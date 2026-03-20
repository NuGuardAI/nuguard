"""Orchestrate SBOM → graph pipeline: mapper → enricher → graph_store.

TODO: Implement full pipeline.
"""

from __future__ import annotations

from nuguard.models.sbom import AiSbomDocument


def build_attack_graph(doc: AiSbomDocument):
    """Build and return an attack graph from *doc*.

    TODO: Wire mapper → enricher → NetworkXGraphStore.
    """
    raise NotImplementedError("build_attack_graph not yet implemented")
