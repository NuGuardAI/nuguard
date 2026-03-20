"""LangChain framework adapter.

Detects LangChain agents, tools, models, and vector stores using Python AST
analysis.  Produces nodes and edges conforming to the Xelo AI-SBOM v1.3.0
schema.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from nuguard.models.sbom import (
    AccessType,
    DatastoreType,
    Edge,
    EdgeRelationshipType,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)

_AGENT_CALLS = frozenset(
    {
        "AgentExecutor",
        "create_react_agent",
        "create_openai_tools_agent",
        "initialize_agent",
    }
)

_TOOL_CLASSES = frozenset(
    {
        "StructuredTool",
        "Tool",
    }
)

_MODEL_CLASSES = frozenset(
    {
        "ChatOpenAI",
        "AzureChatOpenAI",
        "ChatAnthropic",
        "ChatGoogleGenerativeAI",
    }
)

_VECTOR_CLASSES = frozenset({"Chroma", "FAISS", "PineconeVectorStore", "Weaviate"})

_SQL_CLASSES = frozenset({"SQLDatabase", "create_sql_agent"})


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


class LangChainAdapter:
    """Extract LangChain components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges).

        Args:
            file_path: Filesystem path to the source file (used for evidence
                       location only).
            source: Full text of the Python file.

        Returns:
            Tuple of (list[Node], list[Edge]).
        """
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []
        model_ids: list[str] = []
        datastore_ids: list[str] = []

        visitor = _LangChainVisitor(file_path)
        visitor.visit(tree)

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="langchain"),
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
            agent_ids.append(nid)

        for item in visitor.tools:
            nid = _stable_id(item["name"], NodeType.TOOL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.TOOL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="langchain"),
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

        for item in visitor.models:
            nid = _stable_id(item["name"], NodeType.MODEL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.MODEL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="langchain", model_name=item["name"]),
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
            model_ids.append(nid)

        for item in visitor.datastores:
            nid = _stable_id(item["name"], NodeType.DATASTORE)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.DATASTORE,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="langchain",
                        datastore_type=item["datastore_type"],
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
            datastore_ids.append(nid)

        # Build edges: agent → tool (CALLS), agent → model (USES),
        #              agent → datastore (ACCESSES)
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
            for ds_id in datastore_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=ds_id,
                        relationship_type=EdgeRelationshipType.ACCESSES,
                        access_type=AccessType.READWRITE,
                    )
                )

        return nodes, edges


class _LangChainVisitor(ast.NodeVisitor):
    """Walk an AST and collect LangChain component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.models: list[dict] = []
        self.datastores: list[dict] = []
        self._tool_decorator_names: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect @tool decorated functions."""
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
                        "detail": f"@tool decorated function '{node.name}'",
                        "line": node.lineno,
                    }
                )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        for decorator in node.decorator_list:
            dec_name = ""
            if isinstance(decorator, ast.Name):
                dec_name = decorator.id
            elif isinstance(decorator, ast.Attribute):
                dec_name = decorator.attr
            if dec_name == "tool":
                self.tools.append({
                    "name": node.name,
                    "confidence": 0.9,
                    "detail": f"@tool decorated async function '{node.name}'",
                    "line": node.lineno,
                })
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Detect class instantiations and function calls."""
        func_name = self._get_call_name(node)
        line = node.lineno

        if func_name in _AGENT_CALLS:
            self.agents.append(
                {
                    "name": func_name,
                    "confidence": 0.85,
                    "detail": f"LangChain agent via {func_name}()",
                    "line": line,
                }
            )

        elif func_name in _TOOL_CLASSES:
            # Try to extract name= keyword arg
            name = self._get_kwarg_str(node, "name") or func_name
            self.tools.append(
                {
                    "name": name,
                    "confidence": 0.85,
                    "detail": f"LangChain tool via {func_name}(name={name!r})",
                    "line": line,
                }
            )
        elif func_name == "StructuredTool.from_function":
            name = self._get_kwarg_str(node, "name") or "StructuredTool"
            self.tools.append(
                {
                    "name": name,
                    "confidence": 0.85,
                    "detail": "LangChain StructuredTool.from_function()",
                    "line": line,
                }
            )

        elif func_name in _MODEL_CLASSES:
            self.models.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "detail": f"LangChain model {func_name}()",
                    "line": line,
                }
            )

        elif func_name in _VECTOR_CLASSES:
            self.datastores.append(
                {
                    "name": func_name,
                    "confidence": 0.85,
                    "detail": f"LangChain vector store {func_name}()",
                    "line": line,
                    "datastore_type": DatastoreType.VECTOR,
                }
            )

        elif func_name in _SQL_CLASSES:
            self.datastores.append(
                {
                    "name": func_name,
                    "confidence": 0.85,
                    "detail": f"LangChain SQL database {func_name}()",
                    "line": line,
                    "datastore_type": DatastoreType.RELATIONAL,
                }
            )

        self.generic_visit(node)

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        """Extract the callable name from a Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            # Handle Foo.bar() → "Foo.bar"
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
            return node.func.attr
        return ""

    @staticmethod
    def _get_kwarg_str(node: ast.Call, key: str) -> str | None:
        """Extract a string literal keyword argument value."""
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return None
