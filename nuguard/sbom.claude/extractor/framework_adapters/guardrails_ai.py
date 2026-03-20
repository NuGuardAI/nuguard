"""Guardrails AI framework adapter.

Detects Guardrails AI components (Guard, validators) via Python AST analysis.
Produces GUARDRAIL nodes conforming to the Xelo AI-SBOM v1.3.0 schema.
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

_GUARD_CONSTRUCTORS = frozenset(
    {
        "Guard.from_rail",
        "Guard.from_pydantic",
        "Guard.from_rail_string",
        "Guard",
    }
)

_TRIGGER_KEYWORDS = frozenset(
    {"guardrails", "Guard", "validator", "from_rail", "from_pydantic"}
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


class GuardrailsAIAdapter:
    """Extract Guardrails AI components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        if not any(kw in source for kw in _TRIGGER_KEYWORDS):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _GuardrailsVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        guardrail_ids: list[str] = []

        for item in visitor.guardrails:
            nid = _stable_id(item["name"], NodeType.GUARDRAIL)
            extras: dict = {}
            if item.get("validators"):
                extras["validators"] = item["validators"]
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.GUARDRAIL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="guardrails_ai",
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
            guardrail_ids.append(nid)

        # If there are inferred agent references (from guard() call),
        # add PROTECTS edges
        for hint in visitor.protects_hints:
            guardrail_id = _stable_id(hint["guardrail"], NodeType.GUARDRAIL)
            agent_id = _stable_id(hint["agent"], NodeType.AGENT)
            edges.append(
                Edge(
                    source=guardrail_id,
                    target=agent_id,
                    relationship_type=EdgeRelationshipType.PROTECTS,
                )
            )

        return nodes, edges


class _GuardrailsVisitor(ast.NodeVisitor):
    """Walk an AST and collect Guardrails AI component references."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.guardrails: list[dict] = []
        self.protects_hints: list[dict] = []
        # Track current Guard variable name for linking calls
        self._guard_vars: dict[str, str] = {}
        self._validator_classes: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Detect @validator decorated classes."""
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
            if dec_name == "validator":
                self._validator_classes.append(node.name)
                self.guardrails.append(
                    {
                        "name": node.name,
                        "confidence": 0.9,
                        "detail": f"@validator decorated class '{node.name}'",
                        "line": node.lineno,
                        "validators": [node.name],
                    }
                )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Track guard variable assignments."""
        if isinstance(node.value, ast.Call):
            func_name = self._get_call_name(node.value)
            if func_name in _GUARD_CONSTRUCTORS:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._guard_vars[target.id] = target.id
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._get_call_name(node)
        line = node.lineno

        if func_name in _GUARD_CONSTRUCTORS:
            # Extract validators list if present
            validators: list[str] = []
            for kw in node.keywords:
                if kw.arg == "validators" and isinstance(kw.value, ast.List):
                    for elt in kw.value.elts:
                        vname = ""
                        if isinstance(elt, ast.Call):
                            if isinstance(elt.func, ast.Name):
                                vname = elt.func.id
                            elif isinstance(elt.func, ast.Attribute):
                                vname = elt.func.attr
                        elif isinstance(elt, ast.Name):
                            vname = elt.id
                        if vname:
                            validators.append(vname)

            self.guardrails.append(
                {
                    "name": func_name,
                    "confidence": 0.9,
                    "detail": f"Guardrails AI Guard via {func_name}()",
                    "line": line,
                    "validators": validators,
                }
            )

        # guard(llm_api=..., prompt=...) call → inferred PROTECTS relationship
        elif func_name in self._guard_vars or (
            isinstance(node.func, ast.Call) and self._get_call_name(node.func) in self._guard_vars
        ):
            llm_api = self._get_kwarg_str(node, "llm_api")
            if llm_api:
                guard_name = func_name
                self.protects_hints.append(
                    {
                        "guardrail": guard_name,
                        "agent": llm_api,
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
