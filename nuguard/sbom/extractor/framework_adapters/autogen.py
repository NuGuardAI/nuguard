"""AutoGen framework adapter.

Detects:
- ``AssistantAgent(name=...)`` / ``ConversableAgent`` → AGENT node
- ``register_for_llm`` / ``register_for_execution`` → TOOL node
- ``GroupChat`` / ``GroupChatManager`` → AGENT orchestrator node
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
)

_AGENT_CLASSES = frozenset({"AssistantAgent", "ConversableAgent", "UserProxyAgent"})
_ORCHESTRATOR_CLASSES = frozenset({"GroupChat", "GroupChatManager"})
_TOOL_REGISTER_CALLS = frozenset({"register_for_llm", "register_for_execution"})


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


class AutoGenAdapter:
    """Extract AutoGen components from Python source."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _AutoGenVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []
        agent_ids: list[str] = []
        tool_ids: list[str] = []

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="autogen"),
                    evidence=[
                        Evidence(
                            kind=EvidenceKind.AST_INSTANTIATION,
                            confidence=item["confidence"],
                            detail=item["detail"],
                            location=EvidenceLocation(
                                path=str(file_path), line=item.get("line")
                            ),
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
                    metadata=NodeMetadata(framework="autogen"),
                    evidence=[
                        Evidence(
                            kind=EvidenceKind.AST,
                            confidence=item["confidence"],
                            detail=item["detail"],
                            location=EvidenceLocation(
                                path=str(file_path), line=item.get("line")
                            ),
                        )
                    ],
                )
            )
            tool_ids.append(nid)

        # Agents CALLS tools
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


class _AutoGenVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._call_name(node)
        line = node.lineno

        if func_name in _AGENT_CLASSES:
            name = self._get_kwarg_str(node, "name") or func_name
            self.agents.append(
                {
                    "name": name,
                    "confidence": 0.9,
                    "detail": f"AutoGen {func_name}(name={name!r})",
                    "line": line,
                }
            )

        elif func_name in _ORCHESTRATOR_CLASSES:
            self.agents.append(
                {
                    "name": func_name,
                    "confidence": 0.85,
                    "detail": f"AutoGen {func_name} orchestrator",
                    "line": line,
                }
            )

        elif func_name in _TOOL_REGISTER_CALLS:
            # register_for_llm(name=...) or called as a decorator
            name = self._get_kwarg_str(node, "name") or func_name
            self.tools.append(
                {
                    "name": name,
                    "confidence": 0.8,
                    "detail": f"AutoGen tool registered via {func_name}()",
                    "line": line,
                }
            )

        self.generic_visit(node)

    @staticmethod
    def _call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    @staticmethod
    def _get_kwarg_str(node: ast.Call, key: str) -> str | None:
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return None
