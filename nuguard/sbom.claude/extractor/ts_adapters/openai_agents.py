"""TypeScript OpenAI Agents adapter.

Detects OpenAI Agents SDK usage in TypeScript/JavaScript source files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from nuguard.models.sbom import (
    Edge,
    EdgeRelationshipType,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)
from nuguard.sbom.extractor.ts_parser import TSParseResult

_TRIGGER_MODULES = frozenset(
    {
        "@openai/agents",
        "openai/agents",
        "@openai/agents-core",
    }
)


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _evidence(
    kind: EvidenceKind,
    confidence: float,
    detail: str,
    path: Path,
    line: int | None = None,
) -> Evidence:
    return Evidence(
        kind=kind,
        confidence=confidence,
        detail=detail,
        location=EvidenceLocation(path=str(path), line=line),
    )


class OpenAIAgentsTSAdapter:
    """Extract OpenAI Agents SDK usage from a parsed TypeScript/JavaScript file."""

    TRIGGER_MODULES = _TRIGGER_MODULES

    def can_handle(self, result: TSParseResult) -> bool:
        return bool(result.has_module & self.TRIGGER_MODULES)

    def extract(self, file_path: Path, result: TSParseResult) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []

        for call in result.calls:
            name = call.name.replace("new ", "")
            line = call.line

            # new Agent({ name: ..., instructions: ..., tools: [...] })
            if name == "Agent":
                agent_name = call.kwargs.get("name") or "Agent"
                nid = _stable_id(agent_name, NodeType.AGENT)
                nodes.append(
                    Node(
                        id=nid,
                        name=agent_name,
                        component_type=NodeType.AGENT,
                        confidence=0.9,
                        metadata=NodeMetadata(framework="openai_agents"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST_INSTANTIATION,
                                0.9,
                                f"TS OpenAI Agents new Agent(name={agent_name!r})",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                agent_ids.append(nid)

                # instructions → PROMPT if long enough
                instructions = call.kwargs.get("instructions", "")
                if instructions and len(instructions) >= 40:
                    prompt_name = instructions[:40]
                    pnid = _stable_id(prompt_name, NodeType.PROMPT)
                    nodes.append(
                        Node(
                            id=pnid,
                            name=prompt_name,
                            component_type=NodeType.PROMPT,
                            confidence=0.8,
                            metadata=NodeMetadata(framework="openai_agents"),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST,
                                    0.8,
                                    f"TS OpenAI Agents instructions for {agent_name!r}",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )

                # model → MODEL
                model_val = call.kwargs.get("model")
                if model_val:
                    mnid = _stable_id(model_val, NodeType.MODEL)
                    nodes.append(
                        Node(
                            id=mnid,
                            name=model_val,
                            component_type=NodeType.MODEL,
                            confidence=0.85,
                            metadata=NodeMetadata(model_name=model_val),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST,
                                    0.85,
                                    f"TS OpenAI Agents model={model_val!r}",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )
                    edges.append(
                        Edge(
                            source=nid,
                            target=mnid,
                            relationship_type=EdgeRelationshipType.USES,
                        )
                    )

            # tool({ name: "...", execute: fn }) → TOOL
            elif name == "tool" and call.kwargs.get("name"):
                tool_name = call.kwargs["name"]
                tnid = _stable_id(tool_name, NodeType.TOOL)
                nodes.append(
                    Node(
                        id=tnid,
                        name=tool_name,
                        component_type=NodeType.TOOL,
                        confidence=0.85,
                        metadata=NodeMetadata(framework="openai_agents"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST,
                                0.85,
                                f"TS OpenAI Agents tool({{name: {tool_name!r}}})",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                tool_ids.append(tnid)

            # new Handoff({ agent: ... }) → source AGENT -CALLS-> target AGENT
            elif name == "Handoff":
                target_name = call.kwargs.get("agent") or "handoff_agent"
                hnid = _stable_id(target_name, NodeType.AGENT)
                nodes.append(
                    Node(
                        id=hnid,
                        name=target_name,
                        component_type=NodeType.AGENT,
                        confidence=0.8,
                        metadata=NodeMetadata(framework="openai_agents"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST,
                                0.8,
                                f"TS OpenAI Agents Handoff(agent={target_name!r})",
                                file_path,
                                line,
                            )
                        ],
                    )
                )

            # run(agent, ...) or runSync(...)
            elif name in ("run", "runSync"):
                pass  # Evidence of execution, no new nodes needed

        # Edges: agent → tool (CALLS)
        for agent_id in agent_ids:
            for tool_id in tool_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=tool_id,
                        relationship_type=EdgeRelationshipType.CALLS,
                    )
                )

        return nodes, edges
