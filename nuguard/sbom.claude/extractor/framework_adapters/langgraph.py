"""LangGraph framework adapter.

Detects LangGraph agents, tools, models, and datastores using Python AST
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

_GRAPH_CONSTRUCTORS = frozenset(
    {
        "StateGraph",
        "MessageGraph",
        "Graph",
    }
)

_PREBUILT_AGENTS = frozenset(
    {
        "create_react_agent",
        "create_supervisor",
        "create_tool_calling_agent",
    }
)

_MODEL_CLASSES = frozenset(
    {
        "ChatOpenAI",
        "ChatAnthropic",
        "ChatGoogleGenerativeAI",
        "ChatMistralAI",
        "ChatGroq",
        "AzureChatOpenAI",
        "ChatOllama",
    }
)

_VECTOR_CLASSES = frozenset({"FAISS"})


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


class LangGraphAdapter:
    """Extract LangGraph components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges).

        Args:
            file_path: Filesystem path to the source file (used for evidence
                       location only).
            source: Full text of the Python file.

        Returns:
            Tuple of (list[Node], list[Edge]).
        """
        # Quick check: skip files that don't import langgraph
        if not any(
            kw in source
            for kw in ("langgraph", "StateGraph", "MessageGraph", "ToolNode")
        ):
            return [], []

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
        prompt_ids: list[str] = []

        visitor = _LangGraphVisitor(file_path)
        visitor.visit(tree)

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="langgraph"),
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
                    metadata=NodeMetadata(framework="langgraph"),
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
                    metadata=NodeMetadata(
                        framework="langgraph",
                        model_name=item.get("model_name", item["name"]),
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
                        framework="langgraph",
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

        for item in visitor.prompts:
            nid = _stable_id(item["name"], NodeType.PROMPT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.PROMPT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="langgraph"),
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
            prompt_ids.append(nid)

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

        # Relationship hints from add_edge / add_conditional_edges
        for hint in visitor.relationship_hints:
            src_name = hint["source"]
            tgt_name = hint["target"]
            src_id = _stable_id(src_name, NodeType.AGENT)
            tgt_id = _stable_id(tgt_name, NodeType.AGENT)
            edges.append(
                Edge(
                    source=src_id,
                    target=tgt_id,
                    relationship_type=EdgeRelationshipType.CALLS,
                )
            )

        return nodes, edges


class _LangGraphVisitor(ast.NodeVisitor):
    """Walk an AST and collect LangGraph component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.models: list[dict] = []
        self.datastores: list[dict] = []
        self.prompts: list[dict] = []
        self.relationship_hints: list[dict] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect @tool decorated functions."""
        self._check_tool_decorator(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        self._check_tool_decorator(node)
        self.generic_visit(node)

    def _check_tool_decorator(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            dec_name = ""
            if isinstance(decorator, ast.Name):
                dec_name = decorator.id
            elif isinstance(decorator, ast.Attribute):
                dec_name = decorator.attr
            elif isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Name):
                    dec_name = decorator.func.id
                elif isinstance(decorator.func, ast.Attribute):
                    dec_name = decorator.func.attr
            if dec_name == "tool":
                self.tools.append(
                    {
                        "name": node.name,
                        "confidence": 0.9,
                        "detail": f"@tool decorated function '{node.name}'",
                        "line": node.lineno,
                    }
                )

    def visit_Call(self, node: ast.Call) -> None:
        """Detect class instantiations and method calls."""
        func_name = self._get_call_name(node)
        line = node.lineno

        # Graph constructors → AGENT
        if func_name in _GRAPH_CONSTRUCTORS:
            self.agents.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "detail": f"LangGraph graph via {func_name}()",
                    "line": line,
                }
            )

        # Prebuilt agent helpers → AGENT
        elif func_name in _PREBUILT_AGENTS:
            self.agents.append(
                {
                    "name": func_name,
                    "confidence": 0.85,
                    "detail": f"LangGraph agent via {func_name}()",
                    "line": line,
                }
            )

        # ToolNode → TOOL
        elif func_name == "ToolNode":
            self.tools.append(
                {
                    "name": "ToolNode",
                    "confidence": 0.85,
                    "detail": "LangGraph ToolNode()",
                    "line": line,
                }
            )

        # .add_node("name", fn) → AGENT node for the named graph node
        elif func_name == "add_node":
            node_name = self._get_first_str_arg(node) or "graph_node"
            self.agents.append(
                {
                    "name": node_name,
                    "confidence": 0.8,
                    "detail": f"LangGraph graph node '{node_name}' via add_node()",
                    "line": line,
                }
            )

        # .add_edge("from", "to") → RelationshipHint
        elif func_name == "add_edge":
            if len(node.args) >= 2:
                src = self._get_str_arg(node, 0)
                tgt = self._get_str_arg(node, 1)
                if src and tgt:
                    self.relationship_hints.append(
                        {"source": src, "target": tgt}
                    )

        # .add_conditional_edges("from", fn, {mapping}) → hints for each destination
        elif func_name == "add_conditional_edges":
            src = self._get_str_arg(node, 0)
            if src and len(node.args) >= 3:
                mapping_arg = node.args[2]
                if isinstance(mapping_arg, ast.Dict):
                    for val in mapping_arg.values:
                        if isinstance(val, ast.Constant) and isinstance(val.value, str):
                            self.relationship_hints.append(
                                {"source": src, "target": val.value}
                            )

        # Model classes → MODEL
        elif func_name in _MODEL_CLASSES:
            model_name = self._get_kwarg_str(node, "model") or func_name
            self.models.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "model_name": model_name,
                    "detail": f"LangGraph model {func_name}()",
                    "line": line,
                }
            )

        # FAISS vector store → DATASTORE
        elif func_name in _VECTOR_CLASSES or func_name in (
            "FAISS.load_local",
            "FAISS.from_texts",
        ):
            name = func_name.split(".")[0] if "." in func_name else func_name
            self.datastores.append(
                {
                    "name": name,
                    "confidence": 0.85,
                    "detail": f"LangGraph vector store {func_name}()",
                    "line": line,
                    "datastore_type": DatastoreType.VECTOR,
                }
            )

        # SystemMessage / ChatPromptTemplate → PROMPT
        elif func_name in ("SystemMessage", "ChatPromptTemplate.from_messages"):
            content = self._get_kwarg_str(node, "content") or self._get_first_str_arg(node) or func_name
            self.prompts.append(
                {
                    "name": content[:40] if len(content) > 40 else content,
                    "confidence": 0.8,
                    "detail": f"LangGraph prompt via {func_name}()",
                    "line": line,
                }
            )

        self.generic_visit(node)

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        """Extract the callable name from a Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
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

    @staticmethod
    def _get_first_str_arg(node: ast.Call) -> str | None:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        return None

    @staticmethod
    def _get_str_arg(node: ast.Call, idx: int) -> str | None:
        if len(node.args) > idx and isinstance(node.args[idx], ast.Constant) and isinstance(node.args[idx].value, str):
            return node.args[idx].value
        return None
