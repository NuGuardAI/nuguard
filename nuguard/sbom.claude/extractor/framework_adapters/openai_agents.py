"""OpenAI Agents SDK framework adapter.

Detects OpenAI Agents SDK components (Agent, Guardrail, Runner, tools) via
Python AST analysis.  Produces nodes and edges conforming to the Xelo AI-SBOM
v1.3.0 schema.
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

_GUARDRAIL_CLASSES = frozenset({"InputGuardrail", "OutputGuardrail"})

_TRIGGER_KEYWORDS = frozenset(
    {"agents", "openai_agents", "swarm", "Agent", "Runner", "function_tool"}
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


class OpenAIAgentsAdapter:
    """Extract OpenAI Agents SDK components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        # Quick check: skip files without relevant imports
        if not any(kw in source for kw in _TRIGGER_KEYWORDS):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _OpenAIAgentsVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []
        model_ids: list[str] = []
        guardrail_ids: list[str] = []

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="openai_agents"),
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
                    metadata=NodeMetadata(framework="openai_agents"),
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
                        framework="openai_agents",
                        model_name=item["name"],
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

        for item in visitor.guardrails:
            nid = _stable_id(item["name"], NodeType.GUARDRAIL)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.GUARDRAIL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="openai_agents"),
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
            guardrail_ids.append(nid)

        for item in visitor.prompts:
            nid = _stable_id(item["name"], NodeType.PROMPT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.PROMPT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="openai_agents"),
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

        # Edges: agent → tool (CALLS), agent → model (USES)
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

        # Guardrail → PROTECTS → each agent
        for guardrail_id in guardrail_ids:
            for agent_id in agent_ids:
                edges.append(
                    Edge(
                        source=guardrail_id,
                        target=agent_id,
                        relationship_type=EdgeRelationshipType.PROTECTS,
                    )
                )

        # Handoff edges: source agent → target agent (CALLS)
        for hint in visitor.handoff_hints:
            src_id = _stable_id(hint["source"], NodeType.AGENT)
            tgt_id = _stable_id(hint["target"], NodeType.AGENT)
            edges.append(
                Edge(
                    source=src_id,
                    target=tgt_id,
                    relationship_type=EdgeRelationshipType.CALLS,
                )
            )

        return nodes, edges


class _OpenAIAgentsVisitor(ast.NodeVisitor):
    """Walk an AST and collect OpenAI Agents SDK component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.models: list[dict] = []
        self.guardrails: list[dict] = []
        self.prompts: list[dict] = []
        self.handoff_hints: list[dict] = []
        # Track current agent being assigned to handle handoffs
        self._current_agent: str | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect @function_tool / @tool decorated functions."""
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
            if dec_name in ("function_tool", "tool"):
                self.tools.append(
                    {
                        "name": node.name,
                        "confidence": 0.9,
                        "detail": f"@{dec_name} decorated function '{node.name}'",
                        "line": node.lineno,
                    }
                )

    def visit_Call(self, node: ast.Call) -> None:
        """Detect Agent, Guardrail, Runner, OpenAI instantiations."""
        func_name = self._get_call_name(node)
        line = node.lineno

        # Agent(...) → AGENT node
        if func_name == "Agent":
            agent_name = self._get_kwarg_str(node, "name") or "Agent"
            self.agents.append(
                {
                    "name": agent_name,
                    "confidence": 0.9,
                    "detail": f"OpenAI Agents Agent(name={agent_name!r})",
                    "line": line,
                }
            )
            # Extract model kwarg → MODEL node
            model_val = self._get_kwarg_str(node, "model")
            if model_val:
                self.models.append(
                    {
                        "name": model_val,
                        "confidence": 0.9,
                        "detail": f"OpenAI Agents model={model_val!r}",
                        "line": line,
                    }
                )
            # Extract instructions kwarg → PROMPT if long enough
            instructions = self._get_kwarg_str(node, "instructions")
            if instructions and len(instructions) >= 40:
                self.prompts.append(
                    {
                        "name": instructions[:40],
                        "confidence": 0.8,
                        "detail": f"Agent instructions prompt for '{agent_name}'",
                        "line": line,
                    }
                )

        # InputGuardrail / OutputGuardrail → GUARDRAIL
        elif func_name in _GUARDRAIL_CLASSES:
            self.guardrails.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "detail": f"OpenAI Agents {func_name}()",
                    "line": line,
                }
            )

        # Runner.run / Runner.run_sync — add execution evidence
        elif func_name in ("Runner.run", "Runner.run_sync"):
            # First arg is typically the agent
            agent_name = self._get_first_str_arg(node)
            if agent_name:
                self.agents.append(
                    {
                        "name": agent_name,
                        "confidence": 0.75,
                        "detail": f"Agent execution via {func_name}()",
                        "line": line,
                    }
                )

        # OpenAI(...) client → MODEL node
        elif func_name == "OpenAI":
            self.models.append(
                {
                    "name": "OpenAI",
                    "confidence": 0.8,
                    "detail": "OpenAI client instantiation",
                    "line": line,
                }
            )

        # Handoff(agent=...) → source → target AGENT edge
        elif func_name == "Handoff":
            target = self._get_kwarg_str(node, "agent")
            if target:
                # We don't know the source here, so we just add target as an agent
                self.agents.append(
                    {
                        "name": target,
                        "confidence": 0.8,
                        "detail": f"Handoff target agent '{target}'",
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
