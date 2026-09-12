"""Flask framework adapter — discovers routes and request body payload keys.

Registers as a ``FrameworkAdapter`` for Python source files that import
``flask``.  Detects:

- ``Flask()`` / ``Blueprint(...)`` instantiations → FRAMEWORK nodes
- Auth decorators (``login_required``, ``jwt_required``, ...) → AUTH nodes
- ``@app.route(...)`` decorators → API_ENDPOINT nodes
    - ``request.json.get("key")`` / ``request.form.get("key")`` /
        ``data.get("key")`` patterns in handler bodies → ``chat_payload_key``
"""

from __future__ import annotations

import ast
from typing import Any

from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint

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

# OpenAI-style chat-history field names — see fastapi_adapter._MESSAGE_HISTORY_FIELD_NAMES.
# Flask routes are detected via AST .get("key") patterns with no type info, so
# unlike FastAPI we can't confirm the value is list-shaped from a schema — a
# name match alone is treated as sufficient signal to mark chat_payload_list.
_MESSAGE_HISTORY_FIELD_NAMES = {"messages", "history", "conversation", "chat_history"}

# Mirrors the same sets in fastapi_adapter — keep in sync.
_IDENTITY_FIELD_NAMES = frozenset({
    "user_id", "userId", "tenant_id", "tenantId",
    "account_id", "accountId", "customer_id", "customerId",
    "org_id", "orgId", "organization_id", "organizationId",
    "workspace_id", "workspaceId", "project_id", "projectId",
    "subscription_id", "subscriptionId", "user", "username", "login",
})

_SESSION_FIELD_NAMES = frozenset({
    "session_id", "sessionId", "conversation_id", "conversationId",
    "thread_id", "threadId", "chat_id", "chatId", "request_id", "requestId",
})

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
    constants: dict[str, str] | None = None,
) -> tuple[str, str, list[str]] | None:
    """Parse @app.route("/path", methods=[...]) → (receiver, path_str, methods) or None.

    ``constants`` resolves a module-level string-constant name used as the path
    argument (e.g. ``WEBSOCKET_ENDPOINT = "/ws/voice"`` then
    ``@sock.route(WEBSOCKET_ENDPOINT)``) since plain AST has no constant folding.
    """
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
        elif isinstance(first_arg, ast.Name) and constants and first_arg.id in constants:
            path_str = constants[first_arg.id]

    methods: list[str] = []
    for kw in decorator.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List):
            for elt in kw.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    methods.append(elt.value)

    return receiver, path_str, methods


def _collect_module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Return {name: value} for simple module-level ``NAME = "literal"`` assignments.

    Used to resolve constant names passed as decorator arguments (Flask route
    paths are sometimes defined once and reused, e.g. flask_sock's
    ``@sock.route(WEBSOCKET_ENDPOINT)``).
    """
    constants: dict[str, str] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Constant) or not isinstance(stmt.value.value, str):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = stmt.value.value
    return constants


_REQUEST_ACCESSORS = frozenset({
    "json", "form", "values", "data", "payload", "body", "json_data", "request_data",
})


def _collect_body_keys(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Return all keys accessed via request.json.get("key") and similar patterns."""
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
        if receiver_name in _REQUEST_ACCESSORS:
            candidates.append(key)
    return candidates


def _infer_chat_payload_key(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str | None, bool]:
    """Scan function body for request payload .get("key") patterns.

    Returns ``(key, is_message_history)`` — ``is_message_history`` is True when
    the matched key is an OpenAI-style history field (``messages``, ``history``,
    ...), signalling the caller should set ``chat_payload_list=True``.
    """
    candidates = _collect_body_keys(func_def)
    for key in candidates:
        if key in _PROMPT_FIELD_NAMES:
            return key, False
    for key in candidates:
        if key in _MESSAGE_HISTORY_FIELD_NAMES:
            return key, True
    return (candidates[0], False) if candidates else (None, False)


def _infer_context_payload_fields(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    chat_key: str | None,
) -> dict[str, str]:
    """Return ``{field_name: kind}`` for identity/session body fields in a Flask handler."""
    result: dict[str, str] = {}
    lower_identity = {f.lower() for f in _IDENTITY_FIELD_NAMES}
    lower_session = {f.lower() for f in _SESSION_FIELD_NAMES}
    for name in _collect_body_keys(func_def):
        if name == chat_key:
            continue
        nl = name.lower()
        if name in _IDENTITY_FIELD_NAMES or nl in lower_identity:
            result[name] = "identity"
        elif name in _SESSION_FIELD_NAMES or nl in lower_session:
            result[name] = "session"
    return result


# ---------------------------------------------------------------------------
# FrameworkAdapter subclass
# ---------------------------------------------------------------------------


class FlaskAdapter(FrameworkAdapter):
    """Detects Flask routes and request body payload keys via AST analysis."""

    name = "flask"
    priority = 50
    handles_imports = ["flask", "flask.blueprints", "flask_restful", "flask_restx", "flask_sock"]

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
        sock_vars: set[str] = set()       # var names bound to flask_sock.Sock(app) instances
        module_constants = _collect_module_string_constants(tree)

        # First pass: Flask() / Blueprint(...) instantiations
        for stmt in ast.walk(tree):
            if not isinstance(stmt, ast.Assign):
                continue
            if not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            class_name = _get_call_name(call)
            if class_name == "Sock":
                if stmt.targets and isinstance(stmt.targets[0], ast.Name):
                    sock_vars.add(stmt.targets[0].id)
                continue
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

            # Flask() / Blueprint() are web framework objects, not AI agents.
            # Emit as FRAMEWORK so they appear in the infrastructure section.
            canon = f"flask:framework:{file_path}:{agent_name}"
            detections.append(ComponentDetection(
                component_type=ComponentType.FRAMEWORK,
                canonical_name=canon,
                display_name=agent_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=_CONFIDENCE,
                metadata={"framework": "flask", "class": class_name},
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
                ri = _parse_route_decorator(decorator, module_constants)
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
                is_websocket = receiver in sock_vars
                method = "WEBSOCKET" if is_websocket else (methods[0].upper() if methods else "GET")
                func_name = node.name
                # Key on HTTP method + route path only (no framework prefix) so
                # the same endpoint defined across multiple Blueprint/service
                # files — or found by both this adapter and the generic
                # api_endpoint_generic regex fallback — dedupes into one node
                # with evidence from all sources.
                #
                # Empty path_str ("") is a valid Blueprint-root route
                # (@bp.route("", ...)) and must be disambiguated by file path,
                # otherwise every empty-path route in the codebase collapses
                # into one node.
                canon = (
                    f"endpoint:{method}:{path_str}"
                    if path_str
                    else f"endpoint:{method}::{file_path}"
                )

                chat_key: str | None = None
                chat_is_message_history = False
                ctx_fields: dict[str, str] = {}
                # flask_sock handlers read frames off the socket object itself
                # (ws.receive()), not request.json/.form — the HTTP-only body-key
                # inference below does not apply.
                if not is_websocket and "POST" in [m.upper() for m in methods]:
                    chat_key, chat_is_message_history = _infer_chat_payload_key(node)
                    ctx_fields = _infer_context_payload_fields(node, chat_key)

                metadata: dict[str, Any] = {
                    "framework": "flask",
                    "method": method,
                }
                if path_str is not None:
                    metadata["endpoint"] = path_str
                if chat_key:
                    metadata["chat_payload_key"] = chat_key
                    if chat_is_message_history:
                        metadata["chat_payload_list"] = True
                if ctx_fields:
                    metadata["context_payload_fields"] = ctx_fields

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
                        source_type=ComponentType.FRAMEWORK,
                        target_canonical=canon,
                        target_type=ComponentType.API_ENDPOINT,
                        relationship_type="CALLS",
                    ))

        # Third pass: CORS policy (Flask-CORS), debug/verbose-error mode, and
        # security-header posture. Flask sets no security headers by default;
        # a Talisman(...) call (flask-talisman) is treated as evidence headers
        # are handled, its absence as all three headers being missing.
        cors_policy: dict[str, Any] | None = None
        debug_leak = False
        has_header_middleware = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = _get_call_name(node)
                if call_name == "CORS":
                    origin: str | None = None
                    allow_credentials: bool | None = None
                    for kw in node.keywords:
                        if kw.arg in ("origins", "resources"):
                            if isinstance(kw.value, ast.Constant) and kw.value.value == "*":
                                origin = "*"
                            elif isinstance(kw.value, ast.List) and any(
                                isinstance(elt, ast.Constant) and elt.value == "*"
                                for elt in kw.value.elts
                            ):
                                origin = "*"
                        elif kw.arg == "supports_credentials" and isinstance(kw.value, ast.Constant):
                            allow_credentials = bool(kw.value.value)
                    if origin is None and len(node.args) <= 1:
                        # CORS(app) with no restriction kwargs defaults to allow-all.
                        origin = "*"
                    cors_policy = {
                        "origin": origin,
                        "allow_credentials": allow_credentials,
                        "wildcard_with_credentials": origin == "*" and allow_credentials is True,
                    }
                elif call_name == "Talisman":
                    has_header_middleware = True
                elif call_name == "run":
                    for kw in node.keywords:
                        if kw.arg == "debug" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            debug_leak = True
            elif (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
                and any(isinstance(t, ast.Attribute) and t.attr == "debug" for t in node.targets)
            ):
                debug_leak = True

        security_headers_detail = (
            None if has_header_middleware
            else {"missing": ["csp", "x_frame_options", "hsts"]}
        )
        if cors_policy is not None or debug_leak or security_headers_detail is not None:
            for det in detections:
                if det.component_type != ComponentType.API_ENDPOINT:
                    continue
                if cors_policy is not None:
                    det.metadata.setdefault("cors_policy", cors_policy)
                if debug_leak:
                    det.metadata["debug_error_leak"] = True
                if security_headers_detail is not None:
                    det.metadata.setdefault("security_headers_detail", security_headers_detail)

        return detections
