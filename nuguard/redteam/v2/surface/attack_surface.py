"""Normalize an SBOM into the v2 attack-surface graph.

Wraps :class:`~nuguard.analysis.graph.AnalysisGraph` (O(1) traversal) and
:class:`~nuguard.redteam.catalog.capability.CapabilityDetector` (capability
profile) and projects every SBOM node onto the design's attack-surface table —
models, prompts, agents, tools/MCP, APIs, datastores, identity, dependencies,
deployment, observability — tagging each with trust boundary, data sensitivity,
privileges, and side effects so downstream planning can reason about blast radius.

This module is read-only over the SBOM; it performs no network I/O (see
:mod:`nuguard.redteam.v2.surface.recon` for live discovery).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from nuguard.analysis.graph import AnalysisGraph
from nuguard.common.logging import get_logger
from nuguard.redteam.catalog.capability import AppCapabilityProfile, CapabilityDetector
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType

_log = get_logger(__name__)


class SurfaceCategory(str, Enum):
    """Design attack-surface categories (one bucket per row of the SBOM table)."""

    MODELS = "models"
    PROMPTS = "prompts"
    AGENTS = "agents"
    TOOLS = "tools"
    MCP_SERVERS = "mcp_servers"
    APIS = "apis"
    DATASTORES = "datastores"
    IDENTITY = "identity"
    GUARDRAILS = "guardrails"
    DEPENDENCIES = "dependencies"
    DEPLOYMENT = "deployment"
    OBSERVABILITY = "observability"
    OTHER = "other"


class TrustZone(str, Enum):
    """Where a node sits relative to the application's trust boundaries."""

    EXTERNAL_INPUT = "external_input"   # attacker-reachable input (APIs)
    AGENT_RUNTIME = "agent_runtime"     # the reasoning/planning layer
    TOOL_BOUNDARY = "tool_boundary"     # every tool call is a security boundary
    DATA_PLANE = "data_plane"           # datastores / memory
    CONTROL_PLANE = "control_plane"     # guardrails, auth, identity
    MODEL_PLANE = "model_plane"         # models, prompts, frameworks
    INFRASTRUCTURE = "infrastructure"   # deployment, CI, containers


_COMPONENT_TO_SURFACE: dict[ComponentType, SurfaceCategory] = {
    ComponentType.MODEL: SurfaceCategory.MODELS,
    ComponentType.PROMPT: SurfaceCategory.PROMPTS,
    ComponentType.AGENT: SurfaceCategory.AGENTS,
    ComponentType.TOOL: SurfaceCategory.TOOLS,
    ComponentType.MCP_SERVER: SurfaceCategory.MCP_SERVERS,
    ComponentType.API_ENDPOINT: SurfaceCategory.APIS,
    ComponentType.DATASTORE: SurfaceCategory.DATASTORES,
    ComponentType.AUTH: SurfaceCategory.IDENTITY,
    ComponentType.PRIVILEGE: SurfaceCategory.IDENTITY,
    ComponentType.IAM: SurfaceCategory.IDENTITY,
    ComponentType.GUARDRAIL: SurfaceCategory.GUARDRAILS,
    ComponentType.FRAMEWORK: SurfaceCategory.DEPENDENCIES,
    ComponentType.CONTAINER_IMAGE: SurfaceCategory.DEPLOYMENT,
    ComponentType.DEPLOYMENT: SurfaceCategory.DEPLOYMENT,
    ComponentType.GITHUB_WORKFLOW: SurfaceCategory.DEPLOYMENT,
    ComponentType.LIFECYCLE_SCRIPT: SurfaceCategory.DEPLOYMENT,
    ComponentType.DEVELOPER_TOOL_CONFIG: SurfaceCategory.DEPLOYMENT,
}

_SURFACE_TO_ZONE: dict[SurfaceCategory, TrustZone] = {
    SurfaceCategory.APIS: TrustZone.EXTERNAL_INPUT,
    SurfaceCategory.AGENTS: TrustZone.AGENT_RUNTIME,
    SurfaceCategory.TOOLS: TrustZone.TOOL_BOUNDARY,
    SurfaceCategory.MCP_SERVERS: TrustZone.TOOL_BOUNDARY,
    SurfaceCategory.DATASTORES: TrustZone.DATA_PLANE,
    SurfaceCategory.IDENTITY: TrustZone.CONTROL_PLANE,
    SurfaceCategory.GUARDRAILS: TrustZone.CONTROL_PLANE,
    SurfaceCategory.MODELS: TrustZone.MODEL_PLANE,
    SurfaceCategory.PROMPTS: TrustZone.MODEL_PLANE,
    SurfaceCategory.DEPENDENCIES: TrustZone.MODEL_PLANE,
    SurfaceCategory.DEPLOYMENT: TrustZone.INFRASTRUCTURE,
    SurfaceCategory.OBSERVABILITY: TrustZone.INFRASTRUCTURE,
    SurfaceCategory.OTHER: TrustZone.INFRASTRUCTURE,
}


@dataclass(frozen=True)
class SurfaceNode:
    """One SBOM node projected onto the attack surface with risk tags."""

    id: str
    name: str
    component_type: str
    surface: SurfaceCategory
    trust_zone: TrustZone
    data_sensitivity: tuple[str, ...] = ()   # e.g. ("PII", "PHI")
    privileges: tuple[str, ...] = ()          # e.g. ("db_write", "network_out")
    side_effects: tuple[str, ...] = ()        # e.g. ("write", "external_egress")
    protected: bool = False                   # has a GUARDRAIL/AUTH PROTECTS edge


def _data_sensitivity(node: Node) -> tuple[str, ...]:
    md = node.metadata
    if md is None:
        return ()
    labels: list[str] = []
    if getattr(md, "phi_fields", None):
        labels.append("PHI")
    if getattr(md, "pfi_fields", None):
        labels.append("PFI")
    if getattr(md, "pii_fields", None):
        labels.append("PII")
    for label in getattr(md, "data_classification", None) or []:
        val = getattr(label, "value", str(label))
        if val not in labels:
            labels.append(val)
    return tuple(labels)


def _privileges(node: Node) -> tuple[str, ...]:
    md = node.metadata
    if md is None:
        return ()
    privs: list[str] = []
    scope = getattr(md, "privilege_scope", None)
    if scope:
        privs.append(getattr(scope, "value", str(scope)))
    auth_scope = getattr(md, "auth_scope", None)
    if auth_scope:
        privs.append(str(auth_scope))
    if getattr(md, "high_privilege", False):
        privs.append("high_privilege")
    return tuple(dict.fromkeys(privs))  # dedupe, preserve order


@dataclass
class AttackSurface:
    """Normalized, queryable view of an SBOM's attack surface."""

    sbom: AiSbomDocument
    graph: AnalysisGraph
    profile: AppCapabilityProfile
    nodes: tuple[SurfaceNode, ...] = field(default_factory=tuple)

    @classmethod
    def from_sbom(cls, sbom: AiSbomDocument, policy: object | None = None) -> "AttackSurface":
        graph = AnalysisGraph(sbom.model_dump(mode="json"))
        profile = CapabilityDetector(sbom, policy=policy).build()
        write_sinks = set(profile.write_sink_ids)
        egress_sinks = set(profile.egress_sink_ids)
        mcp_tools = set(profile.mcp_tool_ids)

        surface_nodes: list[SurfaceNode] = []
        for node in sbom.nodes:
            nid = str(node.id)
            ctype = node.component_type
            surface = _COMPONENT_TO_SURFACE.get(ctype, SurfaceCategory.OTHER)
            # A tool flagged as MCP belongs in the MCP bucket.
            if surface is SurfaceCategory.TOOLS and nid in mcp_tools:
                surface = SurfaceCategory.MCP_SERVERS
            zone = _SURFACE_TO_ZONE.get(surface, TrustZone.INFRASTRUCTURE)

            side_effects: list[str] = []
            if nid in write_sinks:
                side_effects.append("write")
            if nid in egress_sinks:
                side_effects.append("external_egress")
            if surface is SurfaceCategory.DATASTORES:
                side_effects.append("data_access")

            surface_nodes.append(
                SurfaceNode(
                    id=nid,
                    name=node.name or "",
                    component_type=getattr(ctype, "value", str(ctype)),
                    surface=surface,
                    trust_zone=zone,
                    data_sensitivity=_data_sensitivity(node),
                    privileges=_privileges(node),
                    side_effects=tuple(side_effects),
                    protected=graph.has_protection(nid),
                )
            )

        surface_nodes.sort(key=lambda n: (n.surface.value, n.name, n.id))
        _log.debug(
            "attack surface: %d nodes across %d categories",
            len(surface_nodes),
            len({n.surface for n in surface_nodes}),
        )
        return cls(sbom=sbom, graph=graph, profile=profile, nodes=tuple(surface_nodes))

    # ── Queries ────────────────────────────────────────────────────────────────
    def by_category(self, category: SurfaceCategory) -> list[SurfaceNode]:
        return [n for n in self.nodes if n.surface is category]

    def categories_present(self) -> set[SurfaceCategory]:
        return {n.surface for n in self.nodes}

    def sensitive_nodes(self) -> list[SurfaceNode]:
        """Nodes that hold or expose classified data."""
        return [n for n in self.nodes if n.data_sensitivity]

    def write_capable_nodes(self) -> list[SurfaceNode]:
        """Nodes that can mutate state or send externally (high blast radius)."""
        return [
            n for n in self.nodes
            if "write" in n.side_effects or "external_egress" in n.side_effects
        ]

    def summary(self) -> dict[str, object]:
        """Compact, report-friendly summary of the surface."""
        per_category: dict[str, int] = {}
        for n in self.nodes:
            per_category[n.surface.value] = per_category.get(n.surface.value, 0) + 1
        return {
            "domain": self.profile.domain,
            "node_count": len(self.nodes),
            "per_category": per_category,
            "capabilities": sorted(c.value for c in self.profile.capabilities),
            "sensitive_node_count": len(self.sensitive_nodes()),
            "write_capable_node_count": len(self.write_capable_nodes()),
        }
