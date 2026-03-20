"""Agno (formerly Phidata) framework adapter.

Detects Agno/Phidata agents, teams, models, and datastores via Python AST
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

_MODEL_CLASSES = frozenset(
    {
        "OpenAIChat",
        "Anthropic",
        "Groq",
        "Ollama",
        "AzureOpenAI",
        "Gemini",
        "HuggingFace",
        "Mistral",
        "Cohere",
    }
)

_STORAGE_CLASSES = frozenset(
    {
        "PgAgentStorage",
        "SqliteStorage",
        "MongoDbStorage",
        "RedisStorage",
    }
)

_TRIGGER_KEYWORDS = frozenset(
    {"agno", "phi", "phidata", "Agent", "Team", "Toolkit", "OpenAIChat"}
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


class AgnoAdapter:
    """Extract Agno/Phidata components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        if not any(kw in source for kw in _TRIGGER_KEYWORDS):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _AgnoVisitor(file_path)
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
                    metadata=NodeMetadata(framework="agno"),
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
                    metadata=NodeMetadata(framework="agno"),
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
                        framework="agno",
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
                        framework="agno",
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


class _AgnoVisitor(ast.NodeVisitor):
    """Walk an AST and collect Agno/Phidata component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.models: list[dict] = []
        self.datastores: list[dict] = []

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
        func_name = self._get_call_name(node)
        line = node.lineno

        # Agent(...) → AGENT
        if func_name == "Agent":
            agent_name = self._get_kwarg_str(node, "name") or "Agent"
            self.agents.append(
                {
                    "name": agent_name,
                    "confidence": 0.9,
                    "detail": f"Agno Agent(name={agent_name!r})",
                    "line": line,
                }
            )
            # model kwarg → MODEL
            model_val = self._get_kwarg_str(node, "model")
            if model_val:
                self.models.append(
                    {
                        "name": model_val,
                        "model_name": model_val,
                        "confidence": 0.85,
                        "detail": f"Agno agent model={model_val!r}",
                        "line": line,
                    }
                )

        # Team(agents=[...]) → AGENT orchestrator
        elif func_name == "Team":
            team_name = self._get_kwarg_str(node, "name") or "Team"
            self.agents.append(
                {
                    "name": team_name,
                    "confidence": 0.85,
                    "detail": f"Agno Team(name={team_name!r})",
                    "line": line,
                }
            )

        # Toolkit → TOOL
        elif func_name == "Toolkit":
            toolkit_name = self._get_kwarg_str(node, "name") or "Toolkit"
            self.tools.append(
                {
                    "name": toolkit_name,
                    "confidence": 0.85,
                    "detail": f"Agno Toolkit(name={toolkit_name!r})",
                    "line": line,
                }
            )

        # Model classes → MODEL
        elif func_name in _MODEL_CLASSES:
            model_id_val = self._get_kwarg_str(node, "id") or self._get_kwarg_str(node, "model") or func_name
            self.models.append(
                {
                    "name": func_name,
                    "model_name": model_id_val,
                    "confidence": 0.9,
                    "detail": f"Agno model {func_name}()",
                    "line": line,
                }
            )

        # Knowledge(vector_db=...) → DATASTORE (vector)
        elif func_name == "Knowledge":
            self.datastores.append(
                {
                    "name": "Knowledge",
                    "confidence": 0.85,
                    "detail": "Agno Knowledge() vector datastore",
                    "line": line,
                    "datastore_type": DatastoreType.VECTOR,
                }
            )

        # Storage backends → DATASTORE (relational)
        elif func_name in _STORAGE_CLASSES:
            self.datastores.append(
                {
                    "name": func_name,
                    "confidence": 0.85,
                    "detail": f"Agno storage {func_name}()",
                    "line": line,
                    "datastore_type": DatastoreType.RELATIONAL,
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
