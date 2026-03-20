"""CrewAI framework adapter.

Detects:
- ``Agent(role=..., tools=[...])`` → AGENT node
- ``@tool`` decorator or ``Tool(name=...)`` → TOOL node
- ``Crew(agents=[...], tasks=[...])`` → AGENT orchestrator node
- CALLS edges between crew agent and its tools
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


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


class CrewAIAdapter:
    """Extract CrewAI components from Python source."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _CrewAIVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []
        agent_ids: list[str] = []
        tool_ids: list[str] = []

        for item in visitor.tools:
            nid = _stable_id(item["name"], NodeType.TOOL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.TOOL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="crewai"),
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

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="crewai"),
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
            # CALLS edges to all tools in this file
            for tool_id in tool_ids:
                edges.append(
                    Edge(
                        source=nid,
                        target=tool_id,
                        relationship_type=EdgeRelationshipType.CALLS,
                    )
                )

        for item in visitor.crews:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="crewai", extras={"role": "orchestrator"}),
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

        return nodes, edges


class _CrewAIVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.crews: list[dict] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            dec_name = ""
            if isinstance(decorator, ast.Name):
                dec_name = decorator.id
            elif isinstance(decorator, ast.Attribute):
                dec_name = decorator.attr
            if dec_name == "tool":
                self.tools.append(
                    {
                        "name": node.name,
                        "confidence": 0.9,
                        "detail": f"CrewAI @tool decorated function '{node.name}'",
                        "line": node.lineno,
                    }
                )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        for decorator in node.decorator_list:
            name = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
            if "tool" in name.lower():
                self.tools.append({
                    "name": node.name,
                    "confidence": 0.9,
                    "detail": f"CrewAI @tool decorated async function '{node.name}'",
                    "line": node.lineno,
                })
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._call_name(node)
        line = node.lineno

        if func_name == "Agent":
            role = self._get_kwarg_str(node, "role") or "crew-agent"
            self.agents.append(
                {
                    "name": role,
                    "confidence": 0.85,
                    "detail": f"CrewAI Agent(role={role!r})",
                    "line": line,
                }
            )

        elif func_name == "Tool":
            name = self._get_kwarg_str(node, "name") or "crew-tool"
            self.tools.append(
                {
                    "name": name,
                    "confidence": 0.85,
                    "detail": f"CrewAI Tool(name={name!r})",
                    "line": line,
                }
            )

        elif func_name == "Crew":
            self.crews.append(
                {
                    "name": "Crew",
                    "confidence": 0.8,
                    "detail": "CrewAI Crew orchestrator",
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
