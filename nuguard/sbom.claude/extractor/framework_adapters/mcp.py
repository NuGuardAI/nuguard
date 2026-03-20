"""MCP (Model Context Protocol) framework adapter.

Detects MCP servers, tools, and client-side usage via Python AST analysis.
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


class McpAdapter:
    """Extract MCP server and tool references from a Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges).

        Detects:
        - ``@mcp.tool`` / ``@mcp.tool()`` decorated functions → TOOL nodes
        - ``FastMCP(name=...)`` / ``mcp.server.Server(name=...)`` → AGENT nodes
        - ``ClientSession.call_tool(...)`` / ``use_mcp_server(...)`` → TOOL nodes
        - ``StdioServerParameters`` / ``SSEServerParameters`` → transport metadata
        """
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _McpVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            extras: dict = {}
            if item.get("server_name"):
                extras["server_name"] = item["server_name"]
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="mcp",
                        extras=extras,
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
            agent_ids.append(nid)

        for item in visitor.tools:
            nid = _stable_id(item["name"], NodeType.TOOL)
            extras = {}
            if item.get("server_name"):
                extras["server_name"] = item["server_name"]
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.TOOL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="mcp",
                        transport=item.get("transport"),
                        extras=extras,
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
            tool_ids.append(nid)

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


class _McpVisitor(ast.NodeVisitor):
    """Walk an AST and collect MCP component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        # Track transport type seen in the file
        self._transport: str | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect @mcp.tool / @mcp.tool() decorated functions."""
        self._check_mcp_tool_decorator(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        self._check_mcp_tool_decorator(node)
        self.generic_visit(node)

    def _check_mcp_tool_decorator(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            name = self._decorator_name(decorator)
            # Match @mcp.tool, @app.tool(), @tool, or any *.tool pattern
            if name == "tool" or name.endswith(".tool"):
                self.tools.append(
                    {
                        "name": node.name,
                        "confidence": 0.9,
                        "detail": f"@{name} decorated function '{node.name}'",
                        "line": node.lineno,
                        "transport": self._transport,
                        "server_name": None,
                    }
                )

    @staticmethod
    def _decorator_name(decorator: ast.expr) -> str:
        if isinstance(decorator, ast.Attribute):
            if isinstance(decorator.value, ast.Name):
                return f"{decorator.value.id}.{decorator.attr}"
            return decorator.attr
        if isinstance(decorator, ast.Name):
            return decorator.id
        if isinstance(decorator, ast.Call):
            # @mcp.tool() — call decorator
            return _McpVisitor._decorator_name(decorator.func)  # type: ignore[arg-type]
        return ""

    def visit_Call(self, node: ast.Call) -> None:
        """Detect FastMCP, Server instantiations and client calls."""
        func_name = self._get_call_name(node)
        line = node.lineno

        # MCP server instantiation → AGENT
        if func_name in ("FastMCP", "mcp.server.Server", "Server"):
            server_name = self._get_kwarg_str(node, "name") or func_name
            self.agents.append(
                {
                    "name": server_name,
                    "confidence": 0.9,
                    "detail": f"MCP server via {func_name}(name={server_name!r})",
                    "line": line,
                    "server_name": server_name,
                }
            )

        # Client-side MCP tool reference → TOOL
        # Matches ClientSession.call_tool, session.call_tool, or just call_tool
        elif func_name == "call_tool" or func_name.endswith(".call_tool"):
            # Extract tool name from first positional arg or name= kwarg
            tool_name = self._get_first_str_arg(node) or self._get_kwarg_str(node, "name") or "mcp_tool"
            self.tools.append(
                {
                    "name": tool_name,
                    "confidence": 0.8,
                    "detail": f"MCP client tool call '{tool_name}'",
                    "line": line,
                    "transport": self._transport,
                    "server_name": None,
                }
            )

        # use_mcp_server usage → TOOL
        elif func_name == "use_mcp_server":
            server_name = self._get_kwarg_str(node, "name") or self._get_first_str_arg(node) or "mcp_server"
            self.tools.append(
                {
                    "name": server_name,
                    "confidence": 0.8,
                    "detail": f"use_mcp_server(name={server_name!r})",
                    "line": line,
                    "transport": self._transport,
                    "server_name": server_name,
                }
            )

        # Transport detection
        elif func_name in ("StdioServerParameters",):
            self._transport = "stdio"
        elif func_name in ("SSEServerParameters",):
            self._transport = "sse"

        self.generic_visit(node)

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
            return node.func.attr
        return ""

    @staticmethod
    def _get_kwarg_str(node: ast.Call, key: str) -> str | None:
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return None

    @staticmethod
    def _get_first_str_arg(node: ast.Call) -> str | None:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        return None
