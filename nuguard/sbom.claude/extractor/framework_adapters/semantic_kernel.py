"""Semantic Kernel framework adapter.

Detects Semantic Kernel components (Kernel, plugins, models, prompts) via
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

_MODEL_CLASSES = frozenset(
    {
        "AzureChatCompletion",
        "OpenAIChatCompletion",
        "HuggingFaceTextCompletion",
        "OllamaChatCompletion",
        "AnthropicChatCompletion",
        "GoogleAIChatCompletion",
    }
)

_PROMPT_CLASSES = frozenset(
    {
        "PromptTemplateConfig",
        "KernelPromptTemplate",
        "PromptTemplate",
    }
)

_TRIGGER_KEYWORDS = frozenset(
    {"semantic_kernel", "Kernel", "kernel_function", "AzureChatCompletion", "OpenAIChatCompletion"}
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


class SemanticKernelAdapter:
    """Extract Semantic Kernel components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        if not any(kw in source for kw in _TRIGGER_KEYWORDS):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _SemanticKernelVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []
        tool_ids: list[str] = []
        model_ids: list[str] = []

        for item in visitor.agents:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="semantic_kernel"),
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
                    metadata=NodeMetadata(framework="semantic_kernel"),
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
                        framework="semantic_kernel",
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

        for item in visitor.prompts:
            nid = _stable_id(item["name"], NodeType.PROMPT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.PROMPT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="semantic_kernel"),
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

        # Edges: agent → model (USES), agent → tool (CALLS)
        for agent_id in agent_ids:
            for model_id in model_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=model_id,
                        relationship_type=EdgeRelationshipType.USES,
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


class _SemanticKernelVisitor(ast.NodeVisitor):
    """Walk an AST and collect Semantic Kernel component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.agents: list[dict] = []
        self.tools: list[dict] = []
        self.models: list[dict] = []
        self.prompts: list[dict] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect @kernel_function decorated functions."""
        self._check_kernel_function_decorator(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        self._check_kernel_function_decorator(node)
        self.generic_visit(node)

    def _check_kernel_function_decorator(
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
            if dec_name == "kernel_function":
                self.tools.append(
                    {
                        "name": node.name,
                        "confidence": 0.9,
                        "detail": f"@kernel_function decorated function '{node.name}'",
                        "line": node.lineno,
                    }
                )

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._get_call_name(node)
        line = node.lineno

        # Kernel() → AGENT
        if func_name == "Kernel":
            self.agents.append(
                {
                    "name": "Kernel",
                    "confidence": 0.9,
                    "detail": "Semantic Kernel Kernel() instantiation",
                    "line": line,
                }
            )

        # kernel.add_plugin(...) → TOOL
        elif func_name == "add_plugin" or func_name.endswith(".add_plugin"):
            plugin_name = self._get_kwarg_str(node, "plugin_name") or self._get_first_str_arg(node) or "plugin"
            self.tools.append(
                {
                    "name": plugin_name,
                    "confidence": 0.85,
                    "detail": f"Semantic Kernel plugin via add_plugin()",
                    "line": line,
                }
            )

        # Model service classes → MODEL
        elif func_name in _MODEL_CLASSES:
            model_name = self._get_kwarg_str(node, "deployment_name") or self._get_kwarg_str(node, "ai_model_id") or func_name
            self.models.append(
                {
                    "name": func_name,
                    "model_name": model_name,
                    "confidence": 0.9,
                    "detail": f"Semantic Kernel model service {func_name}()",
                    "line": line,
                }
            )

        # Prompt templates → PROMPT
        elif func_name in _PROMPT_CLASSES:
            template = self._get_kwarg_str(node, "template") or self._get_first_str_arg(node) or func_name
            self.prompts.append(
                {
                    "name": template[:40] if len(template) > 40 else template,
                    "confidence": 0.8,
                    "detail": f"Semantic Kernel prompt {func_name}()",
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
