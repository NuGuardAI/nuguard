"""FastAPI framework adapter for nuguard SBOM extraction.

Detects FastAPI applications, routers, endpoints, and authentication schemes
using Python's AST module.
"""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path
from typing import Any

from nuguard.models.sbom import Edge, EdgeRelationshipType, Node, NodeMetadata, NodeType

logger = logging.getLogger(__name__)

_CONFIDENCE = 0.90

# Classes that map to AGENT nodes
_AGENT_CLASSES = {"FastAPI", "APIRouter"}

# Classes that map to AUTH nodes with their auth_type
_AUTH_CLASSES = {
    "OAuth2PasswordBearer": "oauth2",
    "OAuth2PasswordRequestForm": "oauth2",
    "OAuth2": "oauth2",
    "HTTPBearer": "bearer",
    "HTTPBasic": "basic",
    "HTTPDigest": "digest",
    "APIKeyHeader": "api_key",
    "APIKeyCookie": "api_key",
    "APIKeyQuery": "api_key",
}

# Decorator methods that map to HTTP methods
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _stable_id(key: str) -> str:
    """Generate a stable UUID5 from a canonical key string."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class FastApiAdapter:
    """Extracts FastAPI components (apps, routers, endpoints, auth) from Python source."""

    def extract(self, path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges).

        Returns ([], []) on empty source or syntax error.
        """
        if not source or not source.strip():
            return [], []

        try:
            tree = ast.parse(source)
        except SyntaxError:
            logger.debug("FastApiAdapter: syntax error in %s", path)
            return [], []

        nodes: list[Node] = []
        edges: list[Edge] = []

        # Map: variable_name -> (node_id, class_name)
        agent_vars: dict[str, str] = {}  # var_name -> node_id
        auth_vars: dict[str, str] = {}   # var_name -> auth_type

        # First pass: collect top-level assignments (instantiations)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue

            call = node.value
            class_name = _get_call_name(call)
            if not class_name:
                continue

            # Get assigned variable name
            if not node.targets or not isinstance(node.targets[0], ast.Name):
                continue
            var_name = node.targets[0].id

            if class_name in _AGENT_CLASSES:
                key = f"fastapi:agent:{path}:{var_name}"
                node_id = _stable_id(key)
                agent_node = Node(
                    id=node_id,
                    name=var_name,
                    component_type=NodeType.AGENT,
                    confidence=_CONFIDENCE,
                    metadata=NodeMetadata(framework="fastapi"),
                )
                nodes.append(agent_node)
                agent_vars[var_name] = node_id

            elif class_name in _AUTH_CLASSES:
                auth_type = _AUTH_CLASSES[class_name]
                key = f"fastapi:auth:{path}:{var_name}"
                node_id = _stable_id(key)
                auth_node = Node(
                    id=node_id,
                    name=var_name,
                    component_type=NodeType.AUTH,
                    confidence=_CONFIDENCE,
                    metadata=NodeMetadata(
                        framework="fastapi",
                        auth_type=auth_type,
                        auth_class=class_name,
                    ),
                )
                nodes.append(auth_node)
                auth_vars[var_name] = auth_type

        # Second pass: collect decorated function definitions (endpoints)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                endpoint_info = _parse_route_decorator(decorator)
                if endpoint_info is None:
                    continue

                receiver, method, path_str = endpoint_info
                func_name = node.name
                key = f"fastapi:endpoint:{path}:{func_name}"
                node_id = _stable_id(key)

                # Determine auth_type from function parameters (Depends(...))
                auth_type = _extract_depends_auth_type(node, auth_vars)

                # Also check dependencies= in decorator kwargs
                if auth_type is None:
                    auth_type = _extract_security_auth_type(decorator, auth_vars)

                ep_node = Node(
                    id=node_id,
                    name=func_name,
                    component_type=NodeType.API_ENDPOINT,
                    confidence=_CONFIDENCE,
                    metadata=NodeMetadata(
                        framework="fastapi",
                        endpoint=path_str,
                        method=method.upper(),
                        auth_type=auth_type,
                    ),
                )
                nodes.append(ep_node)

                # Add CALLS edge from agent to endpoint
                if receiver and receiver in agent_vars:
                    agent_id = agent_vars[receiver]
                    edges.append(Edge(
                        source=agent_id,
                        target=node_id,
                        relationship_type=EdgeRelationshipType.CALLS,
                    ))

        return nodes, edges


def _get_call_name(call: ast.Call) -> str | None:
    """Extract the class/function name from a Call node."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _parse_route_decorator(
    decorator: ast.expr,
) -> tuple[str, str, str] | None:
    """Parse a decorator like @app.get('/path') or @router.post('/path').

    Returns (receiver, http_method, path_str) or None if not a route decorator.
    """
    # Handle @app.get("/path") — direct call
    if isinstance(decorator, ast.Call):
        func = decorator.func
        if isinstance(func, ast.Attribute):
            method = func.attr.lower()
            if method in _HTTP_METHODS:
                # Get receiver (app/router variable name)
                receiver = ""
                if isinstance(func.value, ast.Name):
                    receiver = func.value.id

                # Get path from first positional arg
                path_str = ""
                if decorator.args:
                    first_arg = decorator.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        path_str = first_arg.value

                return receiver, method, path_str

    return None


def _extract_depends_auth_type(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    auth_vars: dict[str, str],
) -> str | None:
    """Check function args for Depends(oauth2_scheme) patterns."""
    for arg in func_def.args.defaults + func_def.args.kw_defaults:
        if arg is None:
            continue
        if not isinstance(arg, ast.Call):
            continue
        call_name = _get_call_name(arg)
        if call_name != "Depends":
            continue
        # Check what's inside Depends(...)
        if arg.args:
            inner = arg.args[0]
            if isinstance(inner, ast.Name) and inner.id in auth_vars:
                return auth_vars[inner.id]

    return None


def _extract_security_auth_type(
    decorator: ast.expr,
    auth_vars: dict[str, str],
) -> str | None:
    """Check decorator kwargs for dependencies=[Security(bearer)] patterns."""
    if not isinstance(decorator, ast.Call):
        return None

    for kw in decorator.keywords:
        if kw.arg != "dependencies":
            continue
        val = kw.value
        if not isinstance(val, ast.List):
            continue
        for elt in val.elts:
            if not isinstance(elt, ast.Call):
                continue
            call_name = _get_call_name(elt)
            if call_name not in ("Security", "Depends"):
                continue
            if elt.args:
                inner = elt.args[0]
                if isinstance(inner, ast.Name) and inner.id in auth_vars:
                    return auth_vars[inner.id]

    return None
