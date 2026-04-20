"""FastAPI framework adapter — discovers routes, auth, and request body schemas.

Registers as a ``FrameworkAdapter`` so the core extractor can call it on every
Python source file that imports ``fastapi``.  Uses Python's ``ast`` module to
discover:

- ``FastAPI()`` / ``APIRouter()`` instantiations → AGENT nodes
- ``OAuth2PasswordBearer``, ``HTTPBearer``, ``APIKeyHeader``, etc. → AUTH nodes
- ``@app.get/post/...`` route decorators → API_ENDPOINT nodes
  - Pydantic ``BaseModel`` parameter types → ``request_body_schema``
  - Known prompt field names (message, query, ...) → ``chat_payload_key``
  - Return type annotations on Pydantic models → ``response_text_key``
"""

from __future__ import annotations

import ast
from typing import Any

from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AGENT_CLASSES = {"FastAPI", "APIRouter"}

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

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

_PROMPT_FIELD_NAMES = {
    "message", "query", "prompt", "input", "text",
    "user_query", "user_input", "user_message",
    "transcript", "question", "content", "msg",
}

_RESPONSE_FIELD_NAMES = {"response", "content", "answer", "text", "reply", "message", "output"}

_CONFIDENCE = 0.90


# ---------------------------------------------------------------------------
# AST helpers (private)
# ---------------------------------------------------------------------------

def _annotation_str(node: ast.expr) -> str:
    if hasattr(ast, "unparse"):
        return ast.unparse(node)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return f"{_annotation_str(node.value)}[...]"
    return ""


def _get_call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _collect_model_schemas(tree: ast.AST) -> dict[str, dict[str, str]]:
    """Return ``{class_name: {field_name: type_string}}`` for BaseModel subclasses."""
    schemas: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {
            b.id if isinstance(b, ast.Name) else
            (b.attr if isinstance(b, ast.Attribute) else "")
            for b in node.bases
        }
        if "BaseModel" not in base_names:
            continue
        fields: dict[str, str] = {}
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields[stmt.target.id] = _annotation_str(stmt.annotation)
        if fields:
            schemas[node.name] = fields
    return schemas


def _infer_chat_payload_key(fields: dict[str, str]) -> tuple[str | None, bool]:
    for name in _PROMPT_FIELD_NAMES:
        if name in fields:
            type_str = fields[name]
            is_list = "list" in type_str.lower() or "List" in type_str
            return name, is_list
    for name, type_str in fields.items():
        if type_str.strip() == "str":
            return name, False
    return None, False


def _infer_response_text_key(fields: dict[str, str]) -> str | None:
    for name in _RESPONSE_FIELD_NAMES:
        if name in fields:
            return name
    return None


def _extract_endpoint_schema(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    model_schemas: dict[str, dict[str, str]],
) -> tuple[dict[str, str], str | None, bool, str | None]:
    """Return ``(schema_dict, chat_payload_key, chat_payload_list, response_text_key)``."""
    for arg in func_def.args.args:
        if arg.annotation is None:
            continue
        type_name: str | None = None
        if isinstance(arg.annotation, ast.Name):
            type_name = arg.annotation.id
        if type_name and type_name in model_schemas:
            fields = model_schemas[type_name]
            key, is_list = _infer_chat_payload_key(fields)
            resp_key: str | None = None
            ret = func_def.returns
            if ret is not None:
                ret_name = ret.id if isinstance(ret, ast.Name) else None
                if ret_name and ret_name in model_schemas:
                    resp_key = _infer_response_text_key(model_schemas[ret_name])
            return fields, key, is_list, resp_key
    return {}, None, False, None


def _extract_depends_auth_type(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    auth_vars: dict[str, str],
) -> str | None:
    for arg in func_def.args.defaults + func_def.args.kw_defaults:
        if arg is None:
            continue
        if not isinstance(arg, ast.Call):
            continue
        call_name = _get_call_name(arg)
        if call_name != "Depends":
            continue
        if arg.args:
            inner = arg.args[0]
            if isinstance(inner, ast.Name) and inner.id in auth_vars:
                return auth_vars[inner.id]
    return None


def _extract_security_auth_type(
    decorator: ast.expr,
    auth_vars: dict[str, str],
) -> str | None:
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


# ---------------------------------------------------------------------------
# FrameworkAdapter subclass
# ---------------------------------------------------------------------------


class FastAPIAdapter(FrameworkAdapter):
    """Detects FastAPI routes and request body schemas via AST analysis."""

    name = "fastapi"
    priority = 50
    handles_imports = ["fastapi", "fastapi.routing", "fastapi.security"]

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
        agent_vars: dict[str, str] = {}  # var_name -> canonical_name
        auth_vars: dict[str, str] = {}   # var_name -> auth_type
        model_schemas = _collect_model_schemas(tree)

        # First pass: top-level assignments (FastAPI/APIRouter and auth class instantiations)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            call = node.value
            class_name = _get_call_name(call)
            if not class_name:
                continue
            if not node.targets or not isinstance(node.targets[0], ast.Name):
                continue
            var_name = node.targets[0].id

            if class_name in _AGENT_CLASSES:
                # FastAPI() / APIRouter() are web framework objects, not AI agents.
                # Emit as FRAMEWORK so they appear in the infrastructure section
                # rather than polluting the AI agent list.
                canon = f"fastapi:framework:{file_path}:{var_name}"
                detections.append(ComponentDetection(
                    component_type=ComponentType.FRAMEWORK,
                    canonical_name=canon,
                    display_name=var_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata={"framework": "fastapi", "class": class_name},
                    file_path=file_path,
                    line=node.lineno,
                    evidence_kind="ast_instantiation",
                ))
                agent_vars[var_name] = canon

            elif class_name in _AUTH_CLASSES:
                auth_type = _AUTH_CLASSES[class_name]
                canon = f"fastapi:auth:{file_path}:{var_name}"
                detections.append(ComponentDetection(
                    component_type=ComponentType.AUTH,
                    canonical_name=canon,
                    display_name=var_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata={
                        "framework": "fastapi",
                        "auth_type": auth_type,
                        "auth_class": class_name,
                    },
                    file_path=file_path,
                    line=node.lineno,
                    evidence_kind="ast_instantiation",
                ))
                auth_vars[var_name] = auth_type

        # Second pass: route-decorated function definitions (endpoints)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                ep_info = _parse_route_decorator(decorator)
                if ep_info is None:
                    continue

                receiver, method, path_str = ep_info
                func_name = node.name
                # Key on HTTP method + route path so the same endpoint defined
                # across multiple service files (e.g. GET /health in autogen,
                # crewai, and agent_backend) is deduplicated into one node with
                # evidence from all files, rather than one node per file.
                canon = f"fastapi:endpoint:{method.upper()}:{path_str}"

                ep_auth: str | None = _extract_depends_auth_type(node, auth_vars)
                if ep_auth is None:
                    ep_auth = _extract_security_auth_type(decorator, auth_vars)

                schema, chat_key, chat_list, resp_key = _extract_endpoint_schema(
                    node, model_schemas
                )

                metadata: dict[str, Any] = {
                    "framework": "fastapi",
                    "method": method.upper(),
                }
                if path_str:
                    metadata["endpoint"] = path_str
                if ep_auth:
                    metadata["auth_type"] = ep_auth
                if schema:
                    metadata["request_body_schema"] = schema
                if chat_key:
                    metadata["chat_payload_key"] = chat_key
                    metadata["chat_payload_list"] = chat_list
                if resp_key:
                    metadata["response_text_key"] = resp_key

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
                    snippet=(
                        f"@{receiver}.{method}({path_str!r})"
                        if receiver
                        else f"@{method}({path_str!r})"
                    ),
                    evidence_kind="ast",
                )
                detections.append(ep_detection)

                # Relationship: framework CALLS endpoint
                if receiver and receiver in agent_vars:
                    ep_detection.relationships.append(RelationshipHint(
                        source_canonical=agent_vars[receiver],
                        source_type=ComponentType.FRAMEWORK,
                        target_canonical=canon,
                        target_type=ComponentType.API_ENDPOINT,
                        relationship_type="CALLS",
                    ))

        return detections


def _parse_route_decorator(
    decorator: ast.expr,
) -> tuple[str, str, str] | None:
    """Parse @app.get('/path') → (receiver, method, path_str) or None."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr.lower()
    if method not in _HTTP_METHODS:
        return None

    receiver = ""
    if isinstance(func.value, ast.Name):
        receiver = func.value.id

    path_str = ""
    if decorator.args:
        first_arg = decorator.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            path_str = first_arg.value

    return receiver, method, path_str
