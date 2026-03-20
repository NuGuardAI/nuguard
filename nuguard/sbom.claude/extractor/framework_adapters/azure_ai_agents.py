"""Azure AI Agents framework adapter.

Detects Azure AI Agents components via Python AST analysis.
Produces nodes and edges conforming to the Xelo AI-SBOM v1.3.0 schema.
"""

from __future__ import annotations

import ast
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
    PrivilegeScope,
)

_TOOL_CLASSES = frozenset(
    {
        "FunctionTool",
        "CodeInterpreterTool",
        "BingGroundingTool",
        "FileSearchTool",
        "AzureAISearchTool",
    }
)

_TRIGGER_KEYWORDS = frozenset(
    {"azure.ai.projects", "azure.ai.agents", "create_agent", "FunctionTool", "CodeInterpreterTool"}
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


class AzureAIAgentsAdapter:
    """Extract Azure AI Agents components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        if not any(kw in source for kw in _TRIGGER_KEYWORDS):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _AzureAIAgentsVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []
        model_ids: list[str] = []
        privilege_ids: list[str] = []

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="azure_ai_agents"),
                    evidence=[
                        _evidence(
                            EvidenceKind.AST_INSTANTIATION,
                            item["confidence"],
                            item["detail"],
                            file_path,
                            item.get("line"),
                        )
                    ],
                )
            )
            agent_ids.append(nid)

        for item in visitor.tools:
            nid = _stable_id(item["name"], NodeType.TOOL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.TOOL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="azure_ai_agents"),
                    evidence=[
                        _evidence(
                            EvidenceKind.AST_INSTANTIATION,
                            item["confidence"],
                            item["detail"],
                            file_path,
                            item.get("line"),
                        )
                    ],
                )
            )
            tool_ids.append(nid)

        for item in visitor.models:
            nid = _stable_id(item["name"], NodeType.MODEL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.MODEL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="azure_ai_agents",
                        model_name=item["name"],
                    ),
                    evidence=[
                        _evidence(
                            EvidenceKind.AST,
                            item["confidence"],
                            item["detail"],
                            file_path,
                            item.get("line"),
                        )
                    ],
                )
            )
            model_ids.append(nid)

        for item in visitor.privileges:
            nid = _stable_id(item["name"], NodeType.PRIVILEGE)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.PRIVILEGE,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="azure_ai_agents",
                        privilege_scope=item["scope"],
                    ),
                    evidence=[
                        _evidence(
                            EvidenceKind.AST_INSTANTIATION,
                            item["confidence"],
                            item["detail"],
                            file_path,
                            item.get("line"),
                        )
                    ],
                )
            )
            privilege_ids.append(nid)

        # Edges
        for agent_id in agent_ids:
            for tool_id in tool_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=tool_id,
                        relationship_type=EdgeRelationshipType.CALLS,
                    )
                )
            for model_id in model_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=model_id,
                        relationship_type=EdgeRelationshipType.USES,
                    )
                )

        return nodes, edges


class _AzureAIAgentsVisitor(ast.NodeVisitor):
    """Walk an AST and collect Azure AI Agents component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.models: list[dict] = []
        self.privileges: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._get_call_name(node)
        line = node.lineno

        # project_client.agents.create_agent(...) → AGENT
        if func_name in ("agents.create_agent", "create_agent"):
            model_val = self._get_kwarg_str(node, "model") or "azure_model"
            name_val = self._get_kwarg_str(node, "name") or "azure_agent"
            self.agents.append(
                {
                    "name": name_val,
                    "confidence": 0.9,
                    "detail": f"Azure AI create_agent(name={name_val!r})",
                    "line": line,
                }
            )
            self.models.append(
                {
                    "name": model_val,
                    "confidence": 0.85,
                    "detail": f"Azure AI agent model={model_val!r}",
                    "line": line,
                }
            )

        # bedrock_agent.invoke_agent → AGENT (shared with BedrockAgentCore)
        elif func_name == "invoke_agent" or func_name.endswith(".invoke_agent"):
            agent_id = self._get_kwarg_str(node, "agentId") or "azure_agent"
            self.agents.append(
                {
                    "name": agent_id,
                    "confidence": 0.85,
                    "detail": f"Azure AI invoke_agent(agentId={agent_id!r})",
                    "line": line,
                }
            )

        # Tool classes → TOOL nodes
        elif func_name in _TOOL_CLASSES:
            self.tools.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "detail": f"Azure AI {func_name}()",
                    "line": line,
                }
            )
            # CodeInterpreterTool → PRIVILEGE (code_execution)
            if func_name == "CodeInterpreterTool":
                self.privileges.append(
                    {
                        "name": "code_execution",
                        "scope": PrivilegeScope.CODE_EXECUTION,
                        "confidence": 0.9,
                        "detail": "Azure AI CodeInterpreterTool grants code_execution",
                        "line": line,
                    }
                )

        self.generic_visit(node)

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
            if isinstance(node.func.value, ast.Attribute):
                # Handle 3-level: a.b.c
                outer = node.func.value
                if isinstance(outer.value, ast.Name):
                    return f"{outer.attr}.{node.func.attr}"
            return node.func.attr
        return ""

    @staticmethod
    def _get_kwarg_str(node: ast.Call, key: str) -> str | None:
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return None
