"""Behavior coverage objective planner.

Converts every SBOM node and edge into a typed BehaviorCoverageObjective so
that behavior reports can distinguish:
  - dynamic: exercisable via chat-style scenario execution
  - static: checked by deterministic alignment rules (BA-*)
  - metadata_only: fields or nodes used for risk context, not runtime probing
  - not_behavior_exercisable: infrastructure-only, no chat signal possible
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from nuguard.behavior.models import BehaviorCoverageObjective

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

# Node types and their behavior mode
_NODE_MODE: dict[str, str] = {
    "AGENT": "dynamic",
    "TOOL": "dynamic",
    "GUARDRAIL": "dynamic",
    "DATASTORE": "dynamic",
    "API_ENDPOINT": "dynamic",
    "PROMPT": "dynamic",           # indirect: exercised via AGENT → USES → PROMPT scenarios
    "AUTH": "metadata_only",
    "PRIVILEGE": "metadata_only",
    "MODEL": "metadata_only",
    "FRAMEWORK": "metadata_only",
    "IAM": "not_behavior_exercisable",
    "DEPLOYMENT": "not_behavior_exercisable",
    "CONTAINER_IMAGE": "not_behavior_exercisable",
}

_DEFAULT_NODE_MODE = "metadata_only"

# Edge (relationship) types and their behavior mode
_EDGE_MODE: dict[str, str] = {
    "CALLS": "dynamic",
    "ACCESSES": "dynamic",
    "DELEGATES_TO": "dynamic",
    "PROTECTS": "static",
    "DEPLOYS": "static",
    "USES": "metadata_only",       # refined per target type below
}

_DEFAULT_EDGE_MODE = "metadata_only"


def _node_type_str(node: object) -> str:
    ct = getattr(node, "component_type", None)
    if ct is None:
        return ""
    return str(ct.value if hasattr(ct, "value") else ct).upper()


def _rel_type_str(edge: object) -> str:
    rt = getattr(edge, "relationship_type", None)
    if rt is None:
        return ""
    return str(rt.value if hasattr(rt, "value") else rt).upper()


def build_coverage_objectives(sbom: "AiSbomDocument") -> list[BehaviorCoverageObjective]:
    """Return one BehaviorCoverageObjective per SBOM node and per SBOM edge.

    Node IDs and names are encoded as strings so objectives survive JSON round-trips.
    """
    objectives: list[BehaviorCoverageObjective] = []

    # --- Node objectives ---
    node_id_to_type: dict[str, str] = {}
    node_id_to_name: dict[str, str] = {}
    for node in sbom.nodes:
        nid = str(node.id)
        ntype = _node_type_str(node)
        nname = getattr(node, "name", "") or ""
        node_id_to_type[nid] = ntype
        node_id_to_name[nid] = nname

        mode = _NODE_MODE.get(ntype, _DEFAULT_NODE_MODE)

        reason = ""
        if mode == "not_behavior_exercisable":
            reason = f"{ntype} is infrastructure; no chat-observable signal"
        elif mode == "metadata_only":
            reason = f"{ntype} shapes risk context but is not directly exercisable via chat"
        elif ntype == "PROMPT":
            reason = "Exercised indirectly when the owning AGENT is covered via USES→PROMPT scenarios"
        elif ntype == "DATASTORE":
            reason = "Exercised when ACCESSES-path scenarios reach this datastore"

        objectives.append(
            BehaviorCoverageObjective(
                objective_id=f"node-{nid}",
                surface_type="node",
                node_id=nid,
                node_name=nname,
                node_type=ntype,
                behavior_mode=mode,
                reason=reason,
            )
        )

    # --- Edge objectives ---
    for edge in sbom.edges:
        rel = _rel_type_str(edge)
        src_id = str(edge.source)
        tgt_id = str(edge.target)

        mode = _EDGE_MODE.get(rel, _DEFAULT_EDGE_MODE)

        # USES edge mode depends on target node type
        if rel == "USES":
            target_type = node_id_to_type.get(tgt_id, "")
            if target_type == "PROMPT":
                mode = "dynamic"
            else:
                mode = "metadata_only"

        src_name = node_id_to_name.get(src_id, src_id[:8])
        tgt_name = node_id_to_name.get(tgt_id, tgt_id[:8])
        reason = ""
        if rel == "PROTECTS":
            reason = "Validated by static alignment checks (BA-*)"
        elif rel == "DEPLOYS":
            reason = "Validated by static alignment (BA-015) via deployment metadata"
        elif rel == "USES" and mode == "metadata_only":
            reason = "Target is not chat-exercisable; used for risk context only"

        objectives.append(
            BehaviorCoverageObjective(
                objective_id=f"edge-{uuid.uuid4().hex[:8]}-{rel.lower()}",
                surface_type="edge",
                node_id=None,
                node_name=f"{src_name} → {tgt_name}",
                node_type=None,
                edge_source=src_id,
                edge_target=tgt_id,
                relationship_type=rel,
                behavior_mode=mode,
                reason=reason,
            )
        )

    return objectives
