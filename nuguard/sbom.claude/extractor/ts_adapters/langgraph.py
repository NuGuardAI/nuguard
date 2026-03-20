"""TypeScript LangGraph adapter.

Detects LangGraph and LangChain TS components from parsed TypeScript source.
"""

from __future__ import annotations

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
from nuguard.sbom.extractor.ts_parser import TSParseResult

_TRIGGER_MODULES = frozenset(
    {
        "@langchain/langgraph",
        "@langchain/core",
        "@langchain/openai",
        "@langchain/anthropic",
        "@langchain/community",
        "langchain",
    }
)

_MODEL_CONSTRUCTORS = frozenset(
    {
        "ChatOpenAI",
        "ChatAnthropic",
        "ChatGoogleGenerativeAI",
        "ChatMistralAI",
        "ChatOllama",
        "AzureChatOpenAI",
        "ChatGroq",
    }
)

_AGENT_CONSTRUCTORS = frozenset(
    {
        "StateGraph",
        "MessageGraph",
    }
)

_AGENT_FUNCTIONS = frozenset(
    {
        "createReactAgent",
        "createSupervisor",
        "create_react_agent",
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


class LangGraphTSAdapter:
    """Extract LangGraph/LangChain TS components from a parsed TS/JS file."""

    TRIGGER_MODULES = _TRIGGER_MODULES

    def can_handle(self, result: TSParseResult) -> bool:
        return bool(result.has_module & self.TRIGGER_MODULES)

    def extract(self, file_path: Path, result: TSParseResult) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []
        model_ids: list[str] = []
        datastore_ids: list[str] = []

        for call in result.calls:
            name = call.name.replace("new ", "")
            line = call.line

            # Graph constructors → AGENT
            if name in _AGENT_CONSTRUCTORS:
                nid = _stable_id(name, NodeType.AGENT)
                nodes.append(
                    Node(
                        id=nid,
                        name=name,
                        component_type=NodeType.AGENT,
                        confidence=0.9,
                        metadata=NodeMetadata(framework="langgraph"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST_INSTANTIATION,
                                0.9,
                                f"LangGraph TS new {name}()",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                agent_ids.append(nid)

            # Prebuilt agent functions → AGENT
            elif name in _AGENT_FUNCTIONS:
                nid = _stable_id(name, NodeType.AGENT)
                nodes.append(
                    Node(
                        id=nid,
                        name=name,
                        component_type=NodeType.AGENT,
                        confidence=0.85,
                        metadata=NodeMetadata(framework="langgraph"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST,
                                0.85,
                                f"LangGraph TS {name}()",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                agent_ids.append(nid)

            # addNode → AGENT node
            elif name in ("addNode",):
                node_name = call.args[0].strip().strip('"\'') if call.args else "graph_node"
                # Remove surrounding quotes
                node_name = node_name.strip('"\'`')
                nid = _stable_id(node_name, NodeType.AGENT)
                nodes.append(
                    Node(
                        id=nid,
                        name=node_name,
                        component_type=NodeType.AGENT,
                        confidence=0.8,
                        metadata=NodeMetadata(framework="langgraph"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST,
                                0.8,
                                f"LangGraph TS addNode({node_name!r})",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                agent_ids.append(nid)

            # ToolNode → TOOL
            elif name == "ToolNode":
                nid = _stable_id("ToolNode", NodeType.TOOL)
                nodes.append(
                    Node(
                        id=nid,
                        name="ToolNode",
                        component_type=NodeType.TOOL,
                        confidence=0.85,
                        metadata=NodeMetadata(framework="langgraph"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST_INSTANTIATION,
                                0.85,
                                "LangGraph TS new ToolNode()",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                tool_ids.append(nid)

            # Model constructors → MODEL
            elif name in _MODEL_CONSTRUCTORS:
                model_name = call.kwargs.get("modelName") or call.kwargs.get("model") or name
                nid = _stable_id(name, NodeType.MODEL)
                nodes.append(
                    Node(
                        id=nid,
                        name=name,
                        component_type=NodeType.MODEL,
                        confidence=0.9,
                        metadata=NodeMetadata(
                            framework="langgraph",
                            model_name=model_name,
                        ),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST_INSTANTIATION,
                                0.9,
                                f"LangGraph TS new {name}()",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                model_ids.append(nid)

            # tool() call → TOOL (from TSCall kwargs)
            elif name == "tool" and call.kwargs.get("name"):
                tool_name = call.kwargs["name"]
                nid = _stable_id(tool_name, NodeType.TOOL)
                nodes.append(
                    Node(
                        id=nid,
                        name=tool_name,
                        component_type=NodeType.TOOL,
                        confidence=0.85,
                        metadata=NodeMetadata(framework="langgraph"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST,
                                0.85,
                                f"LangGraph TS tool({{name: {tool_name!r}}})",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                tool_ids.append(nid)

            # InMemoryStore → DATASTORE
            elif name == "InMemoryStore":
                nid = _stable_id("InMemoryStore", NodeType.DATASTORE)
                nodes.append(
                    Node(
                        id=nid,
                        name="InMemoryStore",
                        component_type=NodeType.DATASTORE,
                        confidence=0.8,
                        metadata=NodeMetadata(
                            framework="langgraph",
                            datastore_type=DatastoreType.KV,
                        ),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST_INSTANTIATION,
                                0.8,
                                "LangGraph TS new InMemoryStore()",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                datastore_ids.append(nid)

        # Check decorators for @tool
        for dec_name, line in result.decorators:
            if dec_name == "tool":
                nid = _stable_id(f"tool_decorator_{line}", NodeType.TOOL)
                nodes.append(
                    Node(
                        id=nid,
                        name=f"tool_decorator_{line}",
                        component_type=NodeType.TOOL,
                        confidence=0.85,
                        metadata=NodeMetadata(framework="langgraph"),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST,
                                0.85,
                                "@tool decorated function",
                                file_path,
                                line,
                            )
                        ],
                    )
                )
                tool_ids.append(nid)

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
