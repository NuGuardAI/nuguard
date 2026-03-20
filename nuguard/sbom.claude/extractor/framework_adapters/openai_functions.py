"""OpenAI function-calling / Assistants API adapter.

Detects:
- ``openai.chat.completions.create(tools=[...])`` → extracts TOOL nodes from
  the ``tools`` list.
- ``openai.beta.assistants.create(...)`` → AGENT node.
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


class OpenAIFunctionsAdapter:
    """Extract OpenAI function-calling components from Python source."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _OpenAIVisitor(file_path)
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
                    metadata=NodeMetadata(framework="openai"),
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
            agent_ids.append(nid)

        for item in visitor.tools:
            nid = _stable_id(item["name"], NodeType.TOOL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.TOOL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="openai",
                        extras={"description": item.get("description", "")},
                    ),
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


class _OpenAIVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        call_str = self._call_to_str(node)

        # openai.beta.assistants.create → AGENT
        if "assistants.create" in call_str:
            name = self._get_kwarg_str(node, "name") or "openai-assistant"
            self.agents.append(
                {
                    "name": name,
                    "confidence": 0.9,
                    "detail": "OpenAI Assistants API agent",
                    "line": node.lineno,
                }
            )

        # openai.chat.completions.create(tools=[...]) → extract tool names
        if "completions.create" in call_str or "chat.completions" in call_str:
            tools_arg = self._get_kwarg(node, "tools")
            if tools_arg and isinstance(tools_arg, ast.List):
                for elt in tools_arg.elts:
                    tool_info = self._extract_tool_from_dict(elt)
                    if tool_info:
                        self.tools.append(
                            {
                                "name": tool_info["name"],
                                "description": tool_info.get("description", ""),
                                "confidence": 0.85,
                                "detail": "OpenAI function tool definition",
                                "line": node.lineno,
                            }
                        )

        self.generic_visit(node)

    def _call_to_str(self, node: ast.Call) -> str:
        """Convert the func part of a Call to a dotted string."""
        parts: list[str] = []
        n: ast.expr = node.func
        while isinstance(n, ast.Attribute):
            parts.append(n.attr)
            n = n.value
        if isinstance(n, ast.Name):
            parts.append(n.id)
        return ".".join(reversed(parts))

    def _extract_tool_from_dict(self, node: ast.expr) -> dict | None:
        """Try to extract name/description from an OpenAI tool dict literal."""
        if not isinstance(node, ast.Dict):
            return None
        result: dict = {}
        for key, val in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant):
                continue
            if key.value == "function" and isinstance(val, ast.Dict):
                for fkey, fval in zip(val.keys, val.values):
                    if not isinstance(fkey, ast.Constant):
                        continue
                    if fkey.value == "name" and isinstance(fval, ast.Constant):
                        result["name"] = str(fval.value)
                    elif fkey.value == "description" and isinstance(fval, ast.Constant):
                        result["description"] = str(fval.value)
        return result if "name" in result else None

    @staticmethod
    def _get_kwarg(node: ast.Call, key: str) -> ast.expr | None:
        for kw in node.keywords:
            if kw.arg == key:
                return kw.value
        return None

    @staticmethod
    def _get_kwarg_str(node: ast.Call, key: str) -> str | None:
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return None
