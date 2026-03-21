"""Flask framework adapter — discovers routes and request body payload keys.

Registers as a ``FrameworkAdapter`` for Python source files that import
``flask``.  Detects:

- ``Flask()`` / ``Blueprint(...)`` instantiations → AGENT nodes
- Auth decorators (``login_required``, ``jwt_required``, ...) → AUTH nodes
- ``@app.route(...)`` decorators → API_ENDPOINT nodes
  - ``request.json.get("key")`` / ``data.get("key")`` patterns in handler
    bodies → ``chat_payload_key``
"""

from __future__ import annotations

import ast
from typing import Any

from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint
from ...types import ComponentType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUTH_DECORATORS = {
    "login_required": "session",
    "jwt_required": "jwt",
    "token_required": "token",
    "auth_required": "auth",
}

_PROMPT_FIELD_NAMES = {
    "message", "query", "prompt", "input", "text",
    "user_query", "user_input", "user_message",
    "transcript", "question", "content", "msg",
}

_CONFIDENCE = 0.90


# ---------------------------------------------------------------------------
# AST helpers (private)
# ---------------------------------------------------------------------------

def _get_call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _get_decorator_name(decorator: ast.expr) -> str | None:
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
    """Parse @app.route("/path", methods=[...]) → (receiver, path_str, methods) or None."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr != "route":
        return None

    receiver = ""
    if isinstance(func.value, ast.Name):
        receiver = func.value.id

    path_str = ""
    if decorator.args:
        first_arg = decorator.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            path_str = first_arg.value

    methods: list[str] = []
    for kw in decorator.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List):
            for elt in kw.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    methods.append(elt.value)

    return receiver, path_str, methods


def _infer_chat_payload_key(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """Scan function body for request.json.get("key") / data.get("key") patterns."""
    candidates: list[str] = []

    for node in ast.walk(func_def):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            continue
        key = first_arg.value
        if not key:
            continue

        receiver = func.value
        receiver_name = ""
        if isinstance(receiver, ast.Name):
            receiver_name = receiver.id
        elif isinstance(receiver, ast.Attribute):
            receiver_name = receiver.attr

        if receiver_name in ("json", "data", "payload", "body", "json_data", "request_data"):
            candidates.append(key)

    for key in candidates:
        if key in _PROMPT_FIELD_NAMES:
            return key
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# FrameworkAdapter subclass
# ---------------------------------------------------------------------------


class FlaskAdapter(FrameworkAdapter):
    """Detects Flask routes and request body payload keys via AST analysis."""

    name = "flask"
    priority = 50
    handles_imports = ["flask", "flask.blueprints", "flask_restful", "flask_restx"]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if not content or not content.strip():
            return []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        detections: list[ComponentDetection] = []
        agent_vars: dict[str, str] = {}   # var_name -> canonical_name
        auth_seen: set[str] = set()

        # First pass: Flask() / Blueprint(...) instantiations
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
                agent_name = var_name
                if call.args:
                    first_arg = call.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        agent_name = first_arg.value

            canon = f"flask:agent:{file_path}:{agent_name}"
            detections.append(ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=canon,
                display_name=agent_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=_CONFIDENCE,
                metadata={"framework": "flask"},
                file_path=file_path,
                line=stmt.lineno,
                evidence_kind="ast_instantiation",
            ))
            agent_vars[var_name] = canon

        # Second pass: decorated function definitions (routes + auth)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            route_info = None
            auth_decorator_names: list[str] = []

            for decorator in node.decorator_list:
                ri = _parse_route_decorator(decorator)
                if ri is not None:
                    route_info = ri
                    continue
                dec_name = _get_decorator_name(decorator)
                if dec_name and dec_name in _AUTH_DECORATORS:
                    auth_decorator_names.append(dec_name)

            # Auth nodes from decorators
            for dec_name in auth_decorator_names:
                auth_type = _AUTH_DECORATORS[dec_name]
                auth_key = f"flask:auth:{file_path}:{dec_name}"
                if auth_key not in auth_seen:
                    auth_seen.add(auth_key)
                    detections.append(ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name=auth_key,
                        display_name=dec_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=_CONFIDENCE,
                        metadata={"framework": "flask", "auth_type": auth_type},
                        file_path=file_path,
                        line=node.lineno,
                        evidence_kind="ast",
                    ))

            # Endpoint node
            if route_info is not None:
                receiver, path_str, methods = route_info
                method = methods[0].upper() if methods else "GET"
                func_name = node.name
                canon = f"flask:endpoint:{file_path}:{func_name}"

                chat_key: str | None = None
                if "POST" in [m.upper() for m in methods]:
                    chat_key = _infer_chat_payload_key(node)

                metadata: dict[str, Any] = {
                    "framework": "flask",
                    "method": method,
                }
                if path_str:
                    metadata["endpoint"] = path_str
                if chat_key:
                    metadata["chat_payload_key"] = chat_key

                ep_detection = ComponentDetection(
                    component_type=ComponentType.API_ENDPOINT,
                    canonical_name=canon,
                    display_name=func_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata=metadata,
                    file_path=file_path,
                    line=node.lineno,
                    snippet=f"@{receiver}.route({path_str!r})" if receiver else f"route({path_str!r})",
                    evidence_kind="ast",
                )
                detections.append(ep_detection)

                if receiver and receiver in agent_vars:
                    ep_detection.relationships.append(RelationshipHint(
                        source_canonical=agent_vars[receiver],
                        source_type=ComponentType.AGENT,
                        target_canonical=canon,
                        target_type=ComponentType.API_ENDPOINT,
                        relationship_type="CALLS",
                    ))

        return detections
