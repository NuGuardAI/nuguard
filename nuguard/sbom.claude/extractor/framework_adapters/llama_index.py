"""LlamaIndex framework adapter.

Detects LlamaIndex agents, tools, models, and datastores using Python AST
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

_INDEX_CLASSES = frozenset(
    {
        "VectorStoreIndex",
        "SummaryIndex",
        "KnowledgeGraphIndex",
        "GPTVectorStoreIndex",
        "GPTSimpleVectorIndex",
    }
)

_AGENT_CLASSES = frozenset(
    {
        "QueryEngine",
        "ReActAgent",
        "OpenAIAgent",
        "FunctionAgent",
        "AgentRunner",
        "ReActAgentWorker",
    }
)

_MODEL_CLASSES = frozenset(
    {
        "OpenAI",
        "Anthropic",
        "Gemini",
        "HuggingFaceLLM",
        "OllmaLLM",
        "MistralAI",
    }
)

_TOOL_CLASSES = frozenset(
    {
        "FunctionTool",
        "QueryEngineTool",
        "ToolMetadata",
    }
)

_READER_CLASSES = frozenset(
    {
        "SimpleDirectoryReader",
        "PDFReader",
        "UnstructuredReader",
        "JSONReader",
        "CSVReader",
    }
)

_PROMPT_CLASSES = frozenset(
    {
        "SystemPrompt",
        "PromptTemplate",
        "ChatPromptTemplate",
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


class LlamaIndexAdapter:
    """Extract LlamaIndex components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        if not any(kw in source for kw in ("llama_index", "VectorStoreIndex", "ReActAgent", "OpenAIAgent")):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _LlamaIndexVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []
        model_ids: list[str] = []
        datastore_ids: list[str] = []

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="llama_index"),
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
                    metadata=NodeMetadata(framework="llama_index"),
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
                        framework="llama_index",
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
                        framework="llama_index",
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
                    metadata=NodeMetadata(framework="llama_index"),
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

        # Edges: agent → model (USES), agent → datastore (ACCESSES), agent → tool (CALLS)
        for agent_id in agent_ids:
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
            for tool_id in tool_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=tool_id,
                        relationship_type=EdgeRelationshipType.CALLS,
                    )
                )

        return nodes, edges


class _LlamaIndexVisitor(ast.NodeVisitor):
    """Walk an AST and collect LlamaIndex component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.models: list[dict] = []
        self.datastores: list[dict] = []
        self.prompts: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._get_call_name(node)
        line = node.lineno

        if func_name in _INDEX_CLASSES:
            ds_type = (
                DatastoreType.KNOWLEDGE_BASE
                if "Knowledge" in func_name
                else DatastoreType.VECTOR
            )
            self.datastores.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "detail": f"LlamaIndex index {func_name}()",
                    "line": line,
                    "datastore_type": ds_type,
                }
            )

        elif func_name in _AGENT_CLASSES:
            self.agents.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "detail": f"LlamaIndex agent {func_name}()",
                    "line": line,
                }
            )

        elif func_name == "QueryEngine.from_args" or func_name.endswith(".as_query_engine"):
            self.agents.append(
                {
                    "name": "QueryEngine",
                    "confidence": 0.85,
                    "detail": f"LlamaIndex QueryEngine via {func_name}()",
                    "line": line,
                }
            )

        elif func_name in _MODEL_CLASSES:
            model_name = self._get_kwarg_str(node, "model") or func_name
            self.models.append(
                {
                    "name": func_name,
                    "model_name": model_name,
                    "confidence": 0.9,
                    "detail": f"LlamaIndex LLM {func_name}()",
                    "line": line,
                }
            )

        elif func_name in _TOOL_CLASSES:
            name = self._get_kwarg_str(node, "name") or func_name
            self.tools.append(
                {
                    "name": name,
                    "confidence": 0.85,
                    "detail": f"LlamaIndex tool {func_name}()",
                    "line": line,
                }
            )

        elif func_name in _READER_CLASSES:
            self.datastores.append(
                {
                    "name": func_name,
                    "confidence": 0.8,
                    "detail": f"LlamaIndex reader {func_name}()",
                    "line": line,
                    "datastore_type": DatastoreType.KNOWLEDGE_BASE,
                }
            )

        elif func_name in _PROMPT_CLASSES:
            content = self._get_kwarg_str(node, "template") or self._get_first_str_arg(node) or func_name
            self.prompts.append(
                {
                    "name": content[:40] if len(content) > 40 else content,
                    "confidence": 0.8,
                    "detail": f"LlamaIndex prompt {func_name}()",
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
