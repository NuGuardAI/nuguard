"""Attack graph data models.

TODO: Implement Node, Edge, AttackGraph, and RiskAttribute Pydantic models.
"""

from pydantic import BaseModel


class RiskAttribute(BaseModel):
    """A risk label attached to an attack graph node."""

    name: str
    value: str | None = None


class AttackGraphNode(BaseModel):
    """A node in the enriched attack graph."""

    id: str
    name: str
    node_type: str
    risk_attributes: list[RiskAttribute] = []


class AttackGraphEdge(BaseModel):
    """A directed edge in the attack graph."""

    source: str
    target: str
    edge_type: str
    access_type: str | None = None


class AttackGraph(BaseModel):
    """Full in-memory attack graph derived from an AI-SBOM."""

    graph_id: str
    nodes: list[AttackGraphNode] = []
    edges: list[AttackGraphEdge] = []
