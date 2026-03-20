"""Flask framework adapter.

Detects Flask application instances, blueprints, routes, and auth decorators
via Python AST analysis.
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

_AUTH_DECORATORS = frozenset(
    {
        "login_required",
        "jwt_required",
        "token_required",
        "requires_auth",
        "auth_required",
        "permission_required",
    }
)

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


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


class FlaskAdapter:
    """Extract Flask components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _FlaskVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        edges: list[Edge] = []

        agent_ids: list[str] = []

        for item in visitor.apps:
            nid = _stable_id(item["name"], NodeType.AGENT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AGENT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(framework="flask"),
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

        endpoint_ids: list[str] = []
        for item in visitor.endpoints:
            nid = _stable_id(item["name"], NodeType.API_ENDPOINT)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.API_ENDPOINT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="flask",
                        endpoint=item.get("path"),
                        method=item.get("method"),
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
            endpoint_ids.append(nid)

        for item in visitor.auth_nodes:
            nid = _stable_id(item["name"], NodeType.AUTH)
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.AUTH,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="flask",
                        auth_type=item.get("auth_type"),
                        auth_class=item.get("auth_class"),
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

        # Edges: agent → endpoint (CALLS)
        for agent_id in agent_ids:
            for ep_id in endpoint_ids:
                edges.append(
                    Edge(
                        source=agent_id,
                        target=ep_id,
                        relationship_type=EdgeRelationshipType.CALLS,
                    )
                )

        return nodes, edges


class _FlaskVisitor(ast.NodeVisitor):
    """Walk an AST and collect Flask components."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.apps: list[dict] = []
        self.endpoints: list[dict] = []
        self.auth_nodes: list[dict] = []
        self._app_vars: dict[str, str] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        """Detect app = Flask(__name__) and bp = Blueprint(...) assignments."""
        if isinstance(node.value, ast.Call):
            func_name = self._get_call_name(node.value)
            if func_name == "Flask":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        var_name = target.id
                        self._app_vars[var_name] = var_name
                        self.apps.append(
                            {
                                "name": var_name,
                                "confidence": 0.9,
                                "detail": f"Flask app '{var_name}' via Flask()",
                                "line": node.lineno,
                            }
                        )
            elif func_name == "Blueprint":
                # Blueprint(name, ...)
                bp_name = self._get_first_str_arg(node.value) or "blueprint"
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        var_name = target.id
                        self._app_vars[var_name] = bp_name
                self.apps.append(
                    {
                        "name": bp_name,
                        "confidence": 0.85,
                        "detail": f"Flask Blueprint '{bp_name}'",
                        "line": node.lineno,
                    }
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect @app.route decorators and auth decorators."""
        self._check_decorators(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        self._check_decorators(node)
        self.generic_visit(node)

    def _check_decorators(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        has_route = False
        auth_dec: str | None = None
        path: str | None = None
        methods: list[str] = ["GET"]
        app_var: str | None = None

        for decorator in node.decorator_list:
            dec_name = self._decorator_name(decorator)

            # Route decorator
            if isinstance(decorator, ast.Call):
                func = decorator.func
                if isinstance(func, ast.Attribute) and func.attr == "route":
                    has_route = True
                    if isinstance(func.value, ast.Name):
                        app_var = func.value.id
                    # Path from first positional arg
                    if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                        path = decorator.args[0].value
                    # Methods from methods= kwarg
                    for kw in decorator.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, ast.List):
                            methods = [
                                elt.value.upper()
                                for elt in kw.value.elts
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            ]

            # Auth decorators
            short_name = dec_name.split(".")[-1] if dec_name else ""
            if short_name in _AUTH_DECORATORS:
                auth_dec = short_name

        if has_route:
            method_str = ",".join(methods)
            ep_name = f"{method_str}:{path or node.name}"
            self.endpoints.append(
                {
                    "name": ep_name,
                    "confidence": 0.9,
                    "detail": f"Flask route @{app_var or 'app'}.route({path!r}) methods={methods}",
                    "line": node.lineno,
                    "path": path,
                    "method": method_str,
                }
            )

        if auth_dec:
            self.auth_nodes.append(
                {
                    "name": auth_dec,
                    "confidence": 0.85,
                    "detail": f"Auth decorator @{auth_dec} on '{node.name}'",
                    "line": node.lineno,
                    "auth_type": _classify_flask_auth(auth_dec),
                    "auth_class": auth_dec,
                }
            )

    @staticmethod
    def _decorator_name(decorator: ast.expr) -> str:
        if isinstance(decorator, ast.Name):
            return decorator.id
        if isinstance(decorator, ast.Attribute):
            if isinstance(decorator.value, ast.Name):
                return f"{decorator.value.id}.{decorator.attr}"
            return decorator.attr
        if isinstance(decorator, ast.Call):
            return _FlaskVisitor._decorator_name(decorator.func)  # type: ignore[arg-type]
        return ""

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
    def _get_first_str_arg(node: ast.Call) -> str | None:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        return None


def _classify_flask_auth(dec_name: str) -> str:
    name_lower = dec_name.lower()
    if "jwt" in name_lower:
        return "jwt"
    if "login" in name_lower:
        return "session"
    if "token" in name_lower:
        return "token"
    if "permission" in name_lower:
        return "rbac"
    return "unknown"
