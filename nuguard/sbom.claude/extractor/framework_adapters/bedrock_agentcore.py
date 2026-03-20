"""Amazon Bedrock AgentCore framework adapter.

Detects Bedrock agent runtime usage via Python AST analysis.
Produces nodes and edges conforming to the Xelo AI-SBOM v1.3.0 schema.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from nuguard.models.sbom import (
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

_TRIGGER_KEYWORDS = frozenset(
    {"boto3", "bedrock", "bedrock_agentcore", "invoke_agent", "invoke_model", "bedrock-agent-runtime"}
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


class BedrockAgentCoreAdapter:
    """Extract Amazon Bedrock AgentCore components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        if not any(kw in source for kw in _TRIGGER_KEYWORDS):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _BedrockVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
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
                    metadata=NodeMetadata(framework="bedrock_agentcore"),
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

        for item in visitor.models:
            nid = _stable_id(item["name"], NodeType.MODEL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.MODEL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="bedrock_agentcore",
                        model_name=item.get("model_name", item["name"]),
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

        for item in visitor.datastores:
            nid = _stable_id(item["name"], NodeType.DATASTORE)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.DATASTORE,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="bedrock_agentcore",
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

        # Edges: agent → model (USES)
        for agent_id in agent_ids:
            for model_id in model_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=model_id,
                        relationship_type=EdgeRelationshipType.USES,
                    )
                )

        return nodes, edges


class _BedrockVisitor(ast.NodeVisitor):
    """Walk an AST and collect Bedrock component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.models: list[dict] = []
        self.datastores: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._get_call_name(node)
        line = node.lineno

        # boto3.client("bedrock-agent-runtime") → AGENT
        if func_name == "boto3.client":
            service = self._get_first_str_arg(node)
            if service:
                if "bedrock-agent-runtime" in service or "bedrock-agent" in service:
                    self.agents.append(
                        {
                            "name": "bedrock-agent-runtime",
                            "confidence": 0.85,
                            "detail": f"boto3.client({service!r}) — Bedrock Agent Runtime",
                            "line": line,
                        }
                    )
                elif "bedrock-runtime" in service or service == "bedrock":
                    self.models.append(
                        {
                            "name": "bedrock",
                            "provider": "amazon",
                            "confidence": 0.85,
                            "detail": f"boto3.client({service!r}) — Amazon Bedrock",
                            "line": line,
                        }
                    )

        # bedrock_agent.invoke_agent(agentId=...) → AGENT
        elif func_name == "invoke_agent" or func_name.endswith(".invoke_agent"):
            agent_id_val = self._get_kwarg_str(node, "agentId") or "bedrock_agent"
            self.agents.append(
                {
                    "name": agent_id_val,
                    "confidence": 0.9,
                    "detail": f"Bedrock invoke_agent(agentId={agent_id_val!r})",
                    "line": line,
                }
            )

        # bedrock.invoke_model(modelId=...) → MODEL
        elif func_name == "invoke_model" or func_name.endswith(".invoke_model"):
            model_id_val = self._get_kwarg_str(node, "modelId") or "bedrock_model"
            self.models.append(
                {
                    "name": model_id_val,
                    "model_name": model_id_val,
                    "confidence": 0.9,
                    "detail": f"Bedrock invoke_model(modelId={model_id_val!r})",
                    "line": line,
                }
            )

        # MemoryStore → DATASTORE (kv)
        elif func_name == "MemoryStore":
            self.datastores.append(
                {
                    "name": "MemoryStore",
                    "confidence": 0.85,
                    "detail": "Bedrock MemoryStore()",
                    "line": line,
                    "datastore_type": DatastoreType.KV,
                }
            )

        # KnowledgeBase → DATASTORE (knowledge_base)
        elif func_name == "KnowledgeBase" or func_name.endswith(".KnowledgeBase"):
            self.datastores.append(
                {
                    "name": "KnowledgeBase",
                    "confidence": 0.85,
                    "detail": "Bedrock KnowledgeBase()",
                    "line": line,
                    "datastore_type": DatastoreType.KNOWLEDGE_BASE,
                }
            )

        # retrieve_and_generate / retrieve → implies KnowledgeBase
        elif func_name in ("retrieve_and_generate", "retrieve") or func_name.endswith((".retrieve_and_generate", ".retrieve")):
            kb_id = self._get_kwarg_str(node, "knowledgeBaseId") or "KnowledgeBase"
            self.datastores.append(
                {
                    "name": kb_id,
                    "confidence": 0.8,
                    "detail": f"Bedrock knowledge base retrieval ({func_name})",
                    "line": line,
                    "datastore_type": DatastoreType.KNOWLEDGE_BASE,
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
