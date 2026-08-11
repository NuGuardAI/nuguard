"""Attack graph builder — AI-SBOM to LLM-traversable target graph.

Converts an :class:`~nuguard.sbom.models.AiSbomDocument` into an
:class:`AttackGraph`: a slug-addressed, fully-attributed graph of the target
application's agents, tools, guardrails, datastores, and endpoints, meant to
be handed to (or queried by) an LLM-driven red-team agent during recon.
"""
from __future__ import annotations

from .builder import build_attack_graph
from .models import AttackGraph, GraphEdge, GraphNode
from .serializer import render_graph_text

__all__ = [
    "AttackGraph",
    "GraphEdge",
    "GraphNode",
    "build_attack_graph",
    "render_graph_text",
]
