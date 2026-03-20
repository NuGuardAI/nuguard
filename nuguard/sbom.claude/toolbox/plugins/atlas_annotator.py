"""AtlasAnnotatorPlugin — offline MITRE ATLAS technique mapping.

Maps AI-SBOM graph patterns to MITRE ATLAS techniques without any network
calls.
"""

from __future__ import annotations

from typing import Any

from nuguard.models.sbom import (
    AiSbomDocument,
    EdgeRelationshipType,
    NodeType,
    PrivilegeScope,
)
from nuguard.sbom.toolbox.plugins._base import ToolResult

# Mapping of (pattern_key, description) → ATLAS technique
_ATLAS_TECHNIQUES = {
    "agent_external_api": {
        "technique_id": "AML.T0010",
        "technique_name": "ML Supply Chain Compromise",
    },
    "prompt_injection_risk": {
        "technique_id": "AML.T0051",
        "technique_name": "Prompt Injection",
    },
    "tool_no_auth_datastore": {
        "technique_id": "AML.T0025",
        "technique_name": "Exfiltration via ML Inference",
    },
    "privilege_code_execution": {
        "technique_id": "AML.T0040",
        "technique_name": "ML Model Inference API Access",
    },
    "no_guardrail_agent_output": {
        "technique_id": "AML.T0048",
        "technique_name": "Unsafe ML Artifacts",
    },
}


class AtlasAnnotatorPlugin:
    """Annotate AI-SBOM nodes with MITRE ATLAS technique references (offline)."""

    def run(self, sbom: AiSbomDocument | dict, config: dict | None = None) -> ToolResult:
        """Annotate *sbom* with MITRE ATLAS references.

        Accepts either an :class:`~nuguard.models.sbom.AiSbomDocument` or a
        plain dict.
        """
        config = config or {}
        if isinstance(sbom, dict):
            from nuguard.sbom.extractor.serializer import AiSbomSerializer
            doc = AiSbomSerializer.from_json(sbom)
        else:
            doc = sbom

        annotations: list[dict[str, Any]] = []

        annotations.extend(self._check_agent_external_api(doc))
        annotations.extend(self._check_prompt_injection(doc))
        annotations.extend(self._check_tool_no_auth_datastore(doc))
        annotations.extend(self._check_privilege_code_execution(doc))
        annotations.extend(self._check_no_guardrail_output(doc))

        if not annotations:
            return ToolResult(
                status="pass",
                message="No MITRE ATLAS technique mappings found.",
                details=[],
            )

        return ToolResult(
            status="warn",
            message=f"Found {len(annotations)} MITRE ATLAS technique mapping(s).",
            details=annotations,
        )

    # ------------------------------------------------------------------
    # Pattern checks
    # ------------------------------------------------------------------

    def _check_agent_external_api(self, doc: AiSbomDocument) -> list[dict]:
        """Agent with CALLS to external API_ENDPOINT → AML.T0010."""
        tech = _ATLAS_TECHNIQUES["agent_external_api"]
        results: list[dict] = []
        agent_ids = {n.id for n in doc.nodes if n.component_type == NodeType.AGENT}
        ep_ids = {n.id for n in doc.nodes if n.component_type == NodeType.API_ENDPOINT}

        matched: list[str] = []
        for edge in doc.edges:
            if (
                edge.relationship_type == EdgeRelationshipType.CALLS
                and edge.source in agent_ids
                and edge.target in ep_ids
            ):
                matched.extend([edge.source, edge.target])

        if matched:
            results.append(
                {
                    **tech,
                    "affected_nodes": list(dict.fromkeys(matched)),
                    "confidence": 0.7,
                    "description": "Agent calls external API endpoint — potential supply chain risk.",
                }
            )
        return results

    def _check_prompt_injection(self, doc: AiSbomDocument) -> list[dict]:
        """PROMPT node with injection_risk_score > 0.7 → AML.T0051."""
        tech = _ATLAS_TECHNIQUES["prompt_injection_risk"]
        results: list[dict] = []
        for node in doc.nodes:
            if node.component_type != NodeType.PROMPT:
                continue
            score = node.metadata.extras.get("injection_risk_score", 0.0)
            if isinstance(score, (int, float)) and score > 0.7:
                results.append(
                    {
                        **tech,
                        "affected_nodes": [node.id],
                        "confidence": min(0.95, float(score)),
                        "description": (
                            f"PROMPT node '{node.name}' has injection risk score {score:.2f} > 0.7."
                        ),
                    }
                )
        return results

    def _check_tool_no_auth_datastore(self, doc: AiSbomDocument) -> list[dict]:
        """TOOL with no AUTH accessing DATASTORE → AML.T0025."""
        tech = _ATLAS_TECHNIQUES["tool_no_auth_datastore"]
        results: list[dict] = []
        tool_ids = {n.id for n in doc.nodes if n.component_type == NodeType.TOOL}
        auth_ids = {n.id for n in doc.nodes if n.component_type == NodeType.AUTH}
        ds_ids = {n.id for n in doc.nodes if n.component_type == NodeType.DATASTORE}

        protected_tools: set[str] = set()
        for edge in doc.edges:
            if edge.relationship_type == EdgeRelationshipType.PROTECTS and edge.source in auth_ids:
                protected_tools.add(edge.target)

        matched: list[str] = []
        for edge in doc.edges:
            if (
                edge.relationship_type in (EdgeRelationshipType.ACCESSES, EdgeRelationshipType.CALLS)
                and edge.source in tool_ids
                and edge.target in ds_ids
                and edge.source not in protected_tools
            ):
                matched.extend([edge.source, edge.target])

        if matched:
            results.append(
                {
                    **tech,
                    "affected_nodes": list(dict.fromkeys(matched)),
                    "confidence": 0.75,
                    "description": "TOOL accesses DATASTORE without AUTH — exfiltration risk.",
                }
            )
        return results

    def _check_privilege_code_execution(self, doc: AiSbomDocument) -> list[dict]:
        """PRIVILEGE.code_execution reachable from any node → AML.T0040."""
        tech = _ATLAS_TECHNIQUES["privilege_code_execution"]
        results: list[dict] = []
        priv_nodes = [
            n
            for n in doc.nodes
            if n.component_type == NodeType.PRIVILEGE
            and n.metadata.privilege_scope == PrivilegeScope.CODE_EXECUTION
        ]
        if not priv_nodes:
            return []

        # Check if any edge targets these privilege nodes
        priv_ids = {n.id for n in priv_nodes}
        affected: list[str] = []
        for edge in doc.edges:
            if edge.target in priv_ids:
                affected.extend([edge.source, edge.target])

        if affected or priv_ids:
            all_affected = list(dict.fromkeys(affected or list(priv_ids)))
            results.append(
                {
                    **tech,
                    "affected_nodes": all_affected,
                    "confidence": 0.85,
                    "description": (
                        "PRIVILEGE node with code_execution scope detected — "
                        "ML Model Inference API Access risk."
                    ),
                }
            )
        return results

    def _check_no_guardrail_output(self, doc: AiSbomDocument) -> list[dict]:
        """No GUARDRAIL on agent output path → AML.T0048."""
        tech = _ATLAS_TECHNIQUES["no_guardrail_agent_output"]
        results: list[dict] = []
        agent_ids = {n.id for n in doc.nodes if n.component_type == NodeType.AGENT}
        guardrail_ids = {n.id for n in doc.nodes if n.component_type == NodeType.GUARDRAIL}

        if not agent_ids:
            return []
        if not guardrail_ids:
            # No guardrails at all
            results.append(
                {
                    **tech,
                    "affected_nodes": list(agent_ids),
                    "confidence": 0.65,
                    "description": "No GUARDRAIL nodes found — all agent outputs are unprotected.",
                }
            )
            return results

        # Check which agents have guardrails protecting them or their outputs
        guarded_targets: set[str] = set()
        for edge in doc.edges:
            if edge.relationship_type == EdgeRelationshipType.PROTECTS and edge.source in guardrail_ids:
                guarded_targets.add(edge.target)

        unguarded_agents = [aid for aid in agent_ids if aid not in guarded_targets]
        if unguarded_agents:
            results.append(
                {
                    **tech,
                    "affected_nodes": unguarded_agents,
                    "confidence": 0.6,
                    "description": (
                        f"{len(unguarded_agents)} agent(s) have no GUARDRAIL on their output path."
                    ),
                }
            )
        return results
