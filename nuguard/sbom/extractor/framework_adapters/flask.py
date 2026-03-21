"""Flask framework adapter for nuguard SBOM extraction.

Detects Flask applications, blueprints, route endpoints, and auth decorators
using Python's AST module.
"""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path

from nuguard.models.sbom import Edge, EdgeRelationshipType, Node, NodeMetadata, NodeType

logger = logging.getLogger(__name__)

_CONFIDENCE = 0.90

# Auth decorator names and their auth_type
_AUTH_DECORATORS = {
    "login_required": "session",
    "jwt_required": "jwt",
    "token_required": "token",
    "auth_required": "auth",
}


def _stable_id(key: str) -> str:
    """Generate a stable UUID5 from a canonical key string."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class FlaskAdapter:
    """Extracts Flask components (apps, blueprints, routes, auth) from Python source."""

    def extract(self, path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges).

        Returns ([], []) on empty source or syntax error.
        """
        if not source or not source.strip():
            return [], []

        try:
            tree = ast.parse(source)
        except SyntaxError:
            logger.debug("FlaskAdapter: syntax error in %s", path)
            return [], []

        nodes: list[Node] = []
        edges: list[Edge] = []

        # Map: variable_name -> node_id (for agents/blueprints)
        agent_vars: dict[str, str] = {}
        # Map: app/bp variable -> agent name (for display purposes)

        # Collected auth node canonical names to avoid duplicates
        auth_seen: set[str] = set()

        # First pass: collect Flask(...) and Blueprint(...) instantiations
        for stmt in ast.walk(tree):
            if not isinstance(stmt, ast.Assign):
                continue
            if not isinstance(stmt.value, ast.Call):
                continue

            call = stmt.value
            class_name = _get_call_name(call)
            if class_name not in ("Flask", "Blueprint"):
                continue

            if not stmt.targets or not isinstance(stmt.targets[0], ast.Name):
                continue
            var_name = stmt.targets[0].id

            if class_name == "Flask":
                agent_name = var_name
            else:
                # Blueprint("api", __name__) → name is first positional arg
                agent_name = var_name
                if call.args:
                    first_arg = call.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        agent_name = first_arg.value

            key = f"flask:agent:{path}:{agent_name}"
            node_id = _stable_id(key)
            agent_node = Node(
                id=node_id,
                name=agent_name,
                component_type=NodeType.AGENT,
                confidence=_CONFIDENCE,
                metadata=NodeMetadata(framework="flask"),
            )
            nodes.append(agent_node)
            agent_vars[var_name] = node_id

        # Second pass: collect decorated function definitions (routes + auth decorators)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            route_info = None
            auth_decorator_names: list[str] = []

            for decorator in node.decorator_list:
                # Check for @app.route("/path", methods=[...])
                ri = _parse_route_decorator(decorator)
                if ri is not None:
                    route_info = ri
                    continue

                # Check for auth decorators like @login_required, @jwt_required()
                dec_name = _get_decorator_name(decorator)
                if dec_name and dec_name in _AUTH_DECORATORS:
                    auth_decorator_names.append(dec_name)

            # Emit auth nodes from decorators
            for dec_name in auth_decorator_names:
                auth_type = _AUTH_DECORATORS[dec_name]
                auth_key = f"flask:auth:{path}:{dec_name}"
                if auth_key not in auth_seen:
                    auth_seen.add(auth_key)
                    auth_id = _stable_id(auth_key)
                    auth_node = Node(
                        id=auth_id,
                        name=dec_name,
                        component_type=NodeType.AUTH,
                        confidence=_CONFIDENCE,
                        metadata=NodeMetadata(
                            framework="flask",
                            auth_type=auth_type,
                        ),
                    )
                    nodes.append(auth_node)

            # Emit endpoint node if this function has a route decorator
            if route_info is not None:
                receiver, path_str, methods = route_info
                method = methods[0].upper() if methods else "GET"
                func_name = node.name
                key = f"flask:endpoint:{path}:{func_name}"
                node_id = _stable_id(key)

                ep_node = Node(
                    id=node_id,
                    name=func_name,
                    component_type=NodeType.API_ENDPOINT,
                    confidence=_CONFIDENCE,
                    metadata=NodeMetadata(
                        framework="flask",
                        endpoint=path_str,
                        method=method,
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
    """Extract class/function name from a Call node."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _get_decorator_name(decorator: ast.expr) -> str | None:
    """Get the base name from a decorator (handles both @name and @name())."""
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Call):
        if isinstance(decorator.func, ast.Name):
            return decorator.func.id
        if isinstance(decorator.func, ast.Attribute):
            return decorator.func.attr
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return None


def _parse_route_decorator(
    decorator: ast.expr,
) -> tuple[str, str, list[str]] | None:
    """Parse @app.route("/path", methods=[...]) decorator.

    Returns (receiver, path_str, methods) or None.
    """
    if not isinstance(decorator, ast.Call):
        return None

    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr != "route":
        return None

    # Get receiver (app/bp variable name)
    receiver = ""
    if isinstance(func.value, ast.Name):
        receiver = func.value.id

    # Get path from first positional arg
    path_str = ""
    if decorator.args:
        first_arg = decorator.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            path_str = first_arg.value

    # Get methods from methods=[...] kwarg
    methods: list[str] = []
    for kw in decorator.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List):
            for elt in kw.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    methods.append(elt.value)

    return receiver, path_str, methods
