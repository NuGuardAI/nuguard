"""FastAPI framework adapter.

Detects FastAPI application instances, routers, API endpoints, and auth
dependencies via Python AST analysis.
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

_AUTH_DEPS = frozenset(
    {
        "oauth2_scheme",
        "OAuth2PasswordBearer",
        "HTTPBearer",
        "APIKeyHeader",
        "APIKeyQuery",
        "APIKeyCookie",
        "HTTPBasic",
        "HTTPDigest",
    }
)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


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


class FastApiAdapter:
    """Extract FastAPI components from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _FastApiVisitor(file_path)
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
                    metadata=NodeMetadata(framework="fastapi"),
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
            auth_type = item.get("auth_type")
            nodes.append(
                Node(
                    id=nid,
                    name=item["name"],
                    component_type=NodeType.API_ENDPOINT,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        framework="fastapi",
                        endpoint=item.get("path"),
                        method=item.get("method"),
                        auth_type=auth_type,
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
                        framework="fastapi",
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


class _FastApiVisitor(ast.NodeVisitor):
    """Walk an AST and collect FastAPI components."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.apps: list[dict] = []
        self.endpoints: list[dict] = []
        self.auth_nodes: list[dict] = []
        # Map var name → app/router name
        self._app_vars: dict[str, str] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        """Detect app = FastAPI() and router = APIRouter() assignments."""
        if isinstance(node.value, ast.Call):
            func_name = self._get_call_name(node.value)
            if func_name in ("FastAPI", "APIRouter"):
                # Extract the variable name(s)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        var_name = target.id
                        self._app_vars[var_name] = var_name
                        self.apps.append(
                            {
                                "name": var_name,
                                "confidence": 0.9,
                                "detail": f"FastAPI app/router '{var_name}' via {func_name}()",
                                "line": node.lineno,
                            }
                        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detect route decorators like @app.get('/path')."""
        self._check_route_decorators(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        self._check_route_decorators(node)
        self.generic_visit(node)

    def _check_route_decorators(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            method = None
            app_name = None

            if isinstance(func, ast.Attribute):
                method_candidate = func.attr
                if method_candidate in _HTTP_METHODS:
                    method = method_candidate.upper()
                    if isinstance(func.value, ast.Name):
                        app_name = func.value.id
                elif method_candidate == "route":
                    method = "GET"  # default
                    if isinstance(func.value, ast.Name):
                        app_name = func.value.id

            if method is None:
                continue

            # Extract path from first positional arg
            path: str | None = None
            if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                path = decorator.args[0].value

            # Detect auth type from function parameters
            auth_type = self._detect_auth_in_func(node)

            ep_name = f"{method}:{path or node.name}"
            self.endpoints.append(
                {
                    "name": ep_name,
                    "confidence": 0.9,
                    "detail": f"FastAPI route @{app_name or 'app'}.{method.lower()}({path!r}) → {node.name}",
                    "line": node.lineno,
                    "path": path,
                    "method": method,
                    "auth_type": auth_type,
                }
            )

    def _detect_auth_in_func(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> str | None:
        """Scan function params for Depends/Security auth patterns."""
        for arg in node.args.args + node.args.kwonlyargs:
            annotation = arg.annotation
            if annotation is None:
                continue
            if isinstance(annotation, ast.Call):
                func_name = self._get_call_name(annotation)
                if func_name in ("Depends", "Security"):
                    dep_arg = self._get_first_str_or_name(annotation)
                    if dep_arg and any(a in dep_arg for a in _AUTH_DEPS):
                        auth_class = dep_arg
                        self.auth_nodes.append(
                            {
                                "name": auth_class,
                                "confidence": 0.85,
                                "detail": f"Auth dependency: {func_name}({auth_class})",
                                "line": node.lineno,
                                "auth_type": _classify_auth(auth_class),
                                "auth_class": auth_class,
                            }
                        )
                        return _classify_auth(auth_class)
        # Also scan default values for Depends(...)
        for default in (node.args.defaults + node.args.kw_defaults):
            if default is None:
                continue
            if isinstance(default, ast.Call):
                func_name = self._get_call_name(default)
                if func_name in ("Depends", "Security"):
                    dep_name = self._get_first_str_or_name(default)
                    if dep_name and any(a in dep_name for a in _AUTH_DEPS):
                        self.auth_nodes.append(
                            {
                                "name": dep_name,
                                "confidence": 0.85,
                                "detail": f"Auth dependency: {func_name}({dep_name})",
                                "line": node.lineno,
                                "auth_type": _classify_auth(dep_name),
                                "auth_class": dep_name,
                            }
                        )
                        return _classify_auth(dep_name)
        return None

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
    def _get_first_str_or_name(node: ast.Call) -> str | None:
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Name):
                return arg.id
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            if isinstance(arg, ast.Call):
                return _FastApiVisitor._get_call_name(arg)
        return None


def _classify_auth(name: str) -> str:
    """Map an auth dependency name to a short auth_type string."""
    name_lower = name.lower()
    if "oauth2" in name_lower:
        return "oauth2"
    if "bearer" in name_lower:
        return "bearer"
    if "apikey" in name_lower or "api_key" in name_lower:
        return "api_key"
    if "basic" in name_lower:
        return "basic"
    if "digest" in name_lower:
        return "digest"
    return "unknown"
