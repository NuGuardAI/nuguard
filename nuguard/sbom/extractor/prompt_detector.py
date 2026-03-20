"""Prompt detector — AST-based detection of system prompts and injection risks.

Detects:
- String literals assigned to variables named ``system_prompt``,
  ``SYSTEM_PROMPT``, ``system_message``, ``prompt_template``
- ``{"role": "system", "content": "..."}`` dict patterns
- f-strings / template strings that mix external data (injection risk)
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from nuguard.models.sbom import (
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)

_PROMPT_VAR_NAMES = frozenset(
    {
        "system_prompt",
        "SYSTEM_PROMPT",
        "system_message",
        "SYSTEM_MESSAGE",
        "prompt_template",
        "PROMPT_TEMPLATE",
        "system_instruction",
        "SYSTEM_INSTRUCTION",
    }
)

# Patterns that indicate external/user input in f-strings
_HIGH_RISK_NAMES = frozenset(
    {
        "user_input",
        "user_message",
        "user_query",
        "request",
        "query",
        "message",
        "input",
        "user",
        "prompt",
    }
)

_MEDIUM_RISK_PREFIXES = frozenset({"user_", "input_", "query_", "message_"})


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


class PromptDetector:
    """Detect PROMPT nodes from system prompts and high-risk prompt patterns."""

    def detect(self, file_path: Path, source: str) -> list[Node]:
        """Return PROMPT nodes found in *source*.

        Args:
            file_path: Path to the source file (for evidence location).
            source: Full text of the Python file.

        Returns:
            List of PROMPT :class:`~nuguard.models.sbom.Node` objects.
        """
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return []

        visitor = _PromptVisitor(file_path)
        visitor.visit(tree)
        return visitor.nodes


class _PromptVisitor(ast.NodeVisitor):
    """Walk AST collecting prompt variable assignments and message dicts."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.nodes: list[Node] = []

    # ------------------------------------------------------------------
    # Assignment detection: system_prompt = "..."  or  system_prompt = f"..."
    # ------------------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            var_name = self._get_var_name(target)
            if var_name and var_name in _PROMPT_VAR_NAMES:
                self._handle_prompt_value(var_name, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            self.generic_visit(node)
            return
        var_name = self._get_var_name(node.target)
        if var_name and var_name in _PROMPT_VAR_NAMES:
            self._handle_prompt_value(var_name, node.value, node.lineno)
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Dict pattern: {"role": "system", "content": "..."}
    # ------------------------------------------------------------------

    def visit_Dict(self, node: ast.Dict) -> None:
        if self._is_system_message_dict(node):
            content = self._extract_system_content(node)
            content_preview = (content or "")[:500]
            is_template = False
            injection_risk = 0.0

            if content is None:
                # Content might be an f-string or expression
                is_template = True
                injection_risk = 0.5

            risk_score = injection_risk
            node_name = f"system_message:{node.lineno}"
            nid = _stable_id(node_name, NodeType.PROMPT)
            self.nodes.append(
                Node(
                    id=nid,
                    name=node_name,
                    component_type=NodeType.PROMPT,
                    confidence=0.85,
                    metadata=NodeMetadata(
                        extras={
                            "content": content_preview,
                            "is_template": is_template,
                            "injection_risk_score": risk_score,
                        }
                    ),
                    evidence=[
                        Evidence(
                            kind=EvidenceKind.AST,
                            confidence=0.85,
                            detail=f"System message dict at line {node.lineno}",
                            location=EvidenceLocation(
                                path=str(self.file_path), line=node.lineno
                            ),
                        )
                    ],
                )
            )
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _handle_prompt_value(
        self, var_name: str, value: ast.expr, lineno: int
    ) -> None:
        content, is_template, risk_score = self._analyse_value(value)
        nid = _stable_id(var_name, NodeType.PROMPT)
        self.nodes.append(
            Node(
                id=nid,
                name=var_name,
                component_type=NodeType.PROMPT,
                confidence=0.9,
                metadata=NodeMetadata(
                    extras={
                        "content": (content or "")[:500],
                        "is_template": is_template,
                        "injection_risk_score": risk_score,
                    }
                ),
                evidence=[
                    Evidence(
                        kind=EvidenceKind.AST,
                        confidence=0.9,
                        detail=f"Prompt variable '{var_name}' assigned at line {lineno}",
                        location=EvidenceLocation(
                            path=str(self.file_path), line=lineno
                        ),
                    )
                ],
            )
        )

    @staticmethod
    def _analyse_value(node: ast.expr) -> tuple[str | None, bool, float]:
        """Return (content_str | None, is_template, injection_risk_score)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Static string — no risk
            return node.value, False, 0.0

        if isinstance(node, ast.JoinedStr):
            # f-string: analyse the interpolated names
            return _analyse_fstring(node)

        if isinstance(node, ast.Call):
            # "...".format(...) or Template(...)
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "format":
                risk = _analyse_format_call(node)
                return None, True, risk
            return None, True, 0.3

        # Some other expression — treat as template
        return None, True, 0.3

    @staticmethod
    def _is_system_message_dict(node: ast.Dict) -> bool:
        """Return True if the dict has role=="system" key."""
        for key, val in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "role"
                and isinstance(val, ast.Constant)
                and val.value == "system"
            ):
                return True
        return False

    @staticmethod
    def _extract_system_content(node: ast.Dict) -> str | None:
        for key, val in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "content":
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    return val.value
        return None

    @staticmethod
    def _get_var_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        return None


def _analyse_fstring(node: ast.JoinedStr) -> tuple[str | None, bool, float]:
    """Compute injection risk for an f-string node."""
    risk = 0.0
    names: list[str] = []

    for part in node.values:
        if isinstance(part, ast.FormattedValue):
            value_node = part.value
            if isinstance(value_node, ast.Name):
                names.append(value_node.id)
            elif isinstance(value_node, ast.Attribute):
                # e.g. request.json
                names.append(f"{value_node.value.id if isinstance(value_node.value, ast.Name) else '?'}.{value_node.attr}")

    if not names:
        return None, True, 0.0

    # Check for high-risk names first
    for name in names:
        name_lower = name.lower()
        if any(h in name_lower for h in _HIGH_RISK_NAMES):
            return None, True, 1.0
        for prefix in _MEDIUM_RISK_PREFIXES:
            if name_lower.startswith(prefix):
                risk = max(risk, 0.7)
                break
        else:
            risk = max(risk, 0.3)

    return None, True, risk


def _analyse_format_call(node: ast.Call) -> float:
    """Compute injection risk for "...".format(...) calls."""
    risk = 0.3  # baseline: local variables
    for kw in node.keywords:
        if kw.arg and any(h in kw.arg.lower() for h in _HIGH_RISK_NAMES):
            return 1.0
    for arg in node.args:
        if isinstance(arg, ast.Name):
            if any(h in arg.id.lower() for h in _HIGH_RISK_NAMES):
                return 1.0
            risk = max(risk, 0.7)
    return risk
