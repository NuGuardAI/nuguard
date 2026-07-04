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
import re
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

# Maps auth class name → AuthDetail.protocols value
_AUTH_CLASS_PROTOCOLS: dict[str, list[str]] = {
    "OAuth2PasswordBearer": ["oauth2"],
    "OAuth2PasswordRequestForm": ["oauth2"],
    "OAuth2": ["oauth2"],
    "HTTPBearer": ["bearer"],
    "HTTPBasic": ["basic"],
    "HTTPDigest": ["digest"],
    "APIKeyHeader": ["api_key"],
    "APIKeyCookie": ["api_key"],
    "APIKeyQuery": ["api_key"],
}

# Classes with strict enforcement by default
_AUTH_STRICT_CLASSES = {"OAuth2PasswordBearer", "HTTPBearer", "APIKeyHeader"}

# Rate limit decorator function names
_RATE_LIMIT_UNIT_SECONDS = {
    "second": 1, "seconds": 1,
    "minute": 60, "minutes": 60,
    "hour": 3600, "hours": 3600,
    "day": 86400, "days": 86400,
}

_RATE_LIMIT_RE = re.compile(r"(\d+)\s*/\s*(second|minute|hour|day)s?", re.IGNORECASE)

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

_PROMPT_FIELD_NAMES = {
    "message", "query", "prompt", "input", "text",
    "user_query", "user_input", "user_message",
    "transcript", "question", "content", "msg",
}

_RESPONSE_FIELD_NAMES = {"response", "content", "answer", "text", "reply", "message", "output"}

# Domain-specific request body keys that are clearly NOT conversational message fields.
# Endpoints whose only candidate chat key is in this set should not be marked as
# chat endpoints — doing so causes pre-scan discovery to hit financial/medical
# transaction routes instead of the actual AI chat endpoint.
_NON_CHAT_PAYLOAD_KEYS = frozenset({
    "from_account_id", "to_account_id", "amount", "card_id", "account_id",
    "patient_id", "order_id", "booking_reference", "flight_number",
    "user_id", "transaction_id", "payment_id", "notification_id",
    "recipient_account", "source_account", "debit_account", "credit_account",
})

# Static identity fields — same value for all requests; sourced from login response or config.
_IDENTITY_FIELD_NAMES = frozenset({
    "user_id", "userId", "tenant_id", "tenantId",
    "account_id", "accountId", "customer_id", "customerId",
    "org_id", "orgId", "organization_id", "organizationId",
    "workspace_id", "workspaceId", "project_id", "projectId",
    "subscription_id", "subscriptionId", "user", "username", "login",
})

# Dynamic session fields — change per conversation; auto-generated as UUID when missing.
_SESSION_FIELD_NAMES = frozenset({
    "session_id", "sessionId", "conversation_id", "conversationId",
    "thread_id", "threadId", "chat_id", "chatId", "request_id", "requestId",
})

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


def _infer_context_payload_fields(
    fields: dict[str, str],
    chat_key: str | None,
) -> dict[str, str]:
    """Return ``{field_name: kind}`` for non-chat context fields in a POST body schema.

    ``kind`` is ``'identity'`` (static per user — user_id, tenant_id, etc.) or
    ``'session'`` (dynamic per conversation — session_id, thread_id, etc.).
    The chat field is excluded.
    """
    result: dict[str, str] = {}
    for name in fields:
        if name == chat_key:
            continue
        if name in _IDENTITY_FIELD_NAMES or name.lower() in {f.lower() for f in _IDENTITY_FIELD_NAMES}:
            result[name] = "identity"
        elif name in _SESSION_FIELD_NAMES or name.lower() in {f.lower() for f in _SESSION_FIELD_NAMES}:
            result[name] = "session"
    return result


def _extract_endpoint_schema(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    model_schemas: dict[str, dict[str, str]],
    external_models: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, str], str | None, bool, str | None, dict[str, str], dict[str, str]]:
    """Return ``(req_schema, chat_payload_key, chat_payload_list, response_text_key, context_fields, resp_schema)``.

    *external_models* is a cross-file Pydantic model index used to resolve models that are
    imported from other modules (e.g. ``from schemas import ChatRequest``).
    """
    req_schema: dict[str, str] = {}
    resp_schema: dict[str, str] = {}
    chat_key: str | None = None
    chat_list: bool = False
    resp_key: str | None = None
    ctx_fields: dict[str, str] = {}

    _ext = external_models or {}
    for arg in func_def.args.args:
        if arg.annotation is None:
            continue
        type_name: str | None = None
        if isinstance(arg.annotation, ast.Name):
            type_name = arg.annotation.id
        if type_name:
            resolved = model_schemas.get(type_name) or _ext.get(type_name)
            if resolved:
                req_schema = resolved
                chat_key, chat_list = _infer_chat_payload_key(req_schema)
                ctx_fields = _infer_context_payload_fields(req_schema, chat_key)
                break

    ret = func_def.returns
    if ret is not None:
        ret_name = ret.id if isinstance(ret, ast.Name) else None
        if ret_name:
            resp_schema = model_schemas.get(ret_name) or _ext.get(ret_name) or {}
            if resp_schema:
                resp_key = _infer_response_text_key(resp_schema)

    return req_schema, chat_key, chat_list, resp_key, ctx_fields, resp_schema


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

    def __init__(self) -> None:
        self._global_model_schemas: dict[str, dict[str, str]] = {}

    def set_global_model_schemas(self, schemas: dict[str, dict[str, str]]) -> None:
        """Provide cross-file Pydantic model definitions for imported model resolution."""
        self._global_model_schemas = schemas

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
        # Local definitions take priority over cross-file global schemas
        _effective_external = {k: v for k, v in self._global_model_schemas.items() if k not in model_schemas}

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
                # Prefer the app's title= kwarg; fall back to framework name for generic vars
                _title_kw: str | None = next(
                    (
                        str(kw.value.value)
                        for kw in call.keywords
                        if kw.arg == "title"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ),
                    None,
                )
                _generic_vars = {"app", "api", "server", "router", "application"}
                _fw_display = (
                    _title_kw
                    or (class_name if var_name.lower() in _generic_vars else var_name)
                )
                detections.append(ComponentDetection(
                    component_type=ComponentType.FRAMEWORK,
                    canonical_name=canon,
                    display_name=_fw_display,
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
                auth_detail: dict[str, Any] = {
                    "protocols": _AUTH_CLASS_PROTOCOLS.get(class_name, [auth_type]),
                }
                if class_name in _AUTH_STRICT_CLASSES:
                    auth_detail["enforcement_strict"] = True
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
                        "auth_detail": auth_detail,
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

                schema, chat_key, chat_list, resp_key, ctx_fields, resp_schema = _extract_endpoint_schema(
                    node, model_schemas, _effective_external
                )

                # Also extract response_model= kwarg from the route decorator itself
                # (FastAPI convention: @app.post("/path", response_model=MyModel))
                if not resp_schema and isinstance(decorator, ast.Call):
                    for _kw in decorator.keywords:
                        if _kw.arg == "response_model" and isinstance(_kw.value, ast.Name):
                            _resp_model_name = _kw.value.id
                            if _resp_model_name in model_schemas:
                                resp_schema = model_schemas[_resp_model_name]
                                if not resp_key:
                                    resp_key = _infer_response_text_key(resp_schema)
                            break

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
                if resp_schema:
                    metadata["response_body_schema"] = resp_schema
                if chat_key and chat_key not in _NON_CHAT_PAYLOAD_KEYS:
                    metadata["chat_payload_key"] = chat_key
                    metadata["chat_payload_list"] = chat_list
                if resp_key:
                    metadata["response_text_key"] = resp_key
                if ctx_fields:
                    metadata["context_payload_fields"] = ctx_fields

                # Convert snake_case function name to human-readable display name
                _ep_display = func_name.replace("_", " ").title()

                ep_detection = ComponentDetection(
                    component_type=ComponentType.API_ENDPOINT,
                    canonical_name=canon,
                    display_name=_ep_display,
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

        # Third pass: rate limit detection (slowapi / flask-limiter style decorators)
        has_limiter_middleware = any(
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _get_call_name(node.value) in ("Limiter", "SlowAPILimiter", "RateLimiter")
            for node in ast.walk(tree)
        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                _attr = getattr(func, "attr", None)
                _id = getattr(func, "id", None)
                rl_func_name: str | None = _attr if isinstance(_attr, str) else (_id if isinstance(_id, str) else None)
                if rl_func_name != "limit":
                    continue
                # Extract the limit string argument, e.g. "5/minute"
                limit_str: str | None = None
                for arg in decorator.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        limit_str = arg.value
                        break
                if not limit_str:
                    for kw in decorator.keywords:
                        if kw.arg == "limit" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            limit_str = kw.value.value
                            break
                if not limit_str:
                    continue
                m = _RATE_LIMIT_RE.search(limit_str)
                if not m:
                    continue
                count = int(m.group(1))
                unit = m.group(2).lower().rstrip("s")
                window_secs = _RATE_LIMIT_UNIT_SECONDS.get(unit, 60)
                rld: dict[str, Any] = {
                    "window_seconds": window_secs,
                    "enforcement_type": "middleware" if has_limiter_middleware else "decorator",
                }
                if unit in ("second", "seconds"):
                    rld["requests_per_minute"] = count * 60
                elif unit in ("minute", "minutes"):
                    rld["requests_per_minute"] = count
                elif unit in ("hour", "hours"):
                    rld["requests_per_hour"] = count
                elif unit in ("day", "days"):
                    rld["requests_per_day"] = count
                # Find the matching endpoint detection by function name and update it
                for det in detections:
                    if det.metadata.get("endpoint") and det.metadata.get("method"):
                        if det.component_type == ComponentType.API_ENDPOINT:
                            # Best-effort: tag the endpoint in this file
                            det.metadata.setdefault("rate_limit_detail", rld)
                            det.metadata["rate_limited"] = True

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
