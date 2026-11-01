"""Handler-body evidence shared by the Python HTTP adapters (FastAPI, Flask).

The language-agnostic HTTP-semantics pass (:mod:`nuguard.sbom.http_semantics`)
cannot see inside a handler. This module reads one handler's AST for the few
facts it needs and hands them over as ``metadata["handler_signals"]``:

- ``identity_injection`` — the caller is resolved from the credential, not the request
- ``audit_logged``       — the handler writes an audit-log record
- ``sets_cookie``        — the handler sets a cookie or writes to a server-side session
- ``auth_checked_in_handler`` — a token is verified inside the body (WebSocket handlers
  cannot use route-level dependencies the way HTTP routes can)

Only names are matched — never argument values — so nothing secret can leak into
the SBOM.
"""

from __future__ import annotations

import ast
import re
from typing import Any

# Dependency names that resolve "who is calling" from the credential (broad: the
# name is already known to be a request dependency).
_IDENTITY_DEPENDENCY_RE = re.compile(
    r"current_?(?:user|account|subject|principal|customer)|get_?(?:user|principal|subject)"
    r"|authenticated_?user|auth_?user|whoami|get_jwt_identity|current_identity",
    re.IGNORECASE,
)
# Direct calls inside a handler body (strict: ``get_user_by_id`` is a DB lookup, not the caller).
_IDENTITY_CALL_RE = re.compile(
    r"^(?:get_current_\w+|current_user|get_jwt_identity|current_identity|authenticated_user|whoami)$",
    re.IGNORECASE,
)
_AUDIT_RE = re.compile(r"audit", re.IGNORECASE)
_AUTH_CHECK_RE = re.compile(
    r"^authenticate\w*$|(?:verify|decode|validate|check)_?(?:token|jwt|auth|credentials?|api_?key)"
    r"|^get_current_user$|^get_user_from_token$",
    re.IGNORECASE,
)
_SSE_MEDIA_TYPE = "text/event-stream"
_COOKIE_ATTRS = frozenset({"set_cookie"})
_SESSION_NAMES = frozenset({"session"})
_SESSION_CALLS = frozenset({"login_user", "SessionMiddleware"})


def _called_names(func_def: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(func_def):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _identity_injection(func_def: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Names of credential-resolving dependencies / helpers the handler uses."""
    found: list[str] = []
    args = func_def.args
    # ``x = Depends(dep)`` defaults and ``x: Annotated[T, Depends(dep)]`` annotations.
    candidates: list[ast.AST] = [d for d in (*args.defaults, *args.kw_defaults) if d is not None]
    candidates.extend(
        a.annotation
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if a.annotation is not None
    )
    for root in candidates:
        for call in ast.walk(root):
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) in ("Depends", "Security"):
                inner = call.args[0] if call.args else None
                name = inner.id if isinstance(inner, ast.Name) else getattr(inner, "attr", None)
                if name and _IDENTITY_DEPENDENCY_RE.search(name):
                    found.append(f"Depends({name})")
    for node in ast.walk(func_def):
        # flask_login.current_user / flask.g.user / get_jwt_identity()
        if isinstance(node, ast.Name) and node.id == "current_user":
            found.append("current_user")
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name and _IDENTITY_CALL_RE.match(name):
                found.append(f"{name}()")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "user"
            and isinstance(node.value, ast.Name)
            and node.value.id in ("g", "request")
        ):
            found.append(f"{node.value.id}.user")
    return list(dict.fromkeys(found))


def _sets_cookie(func_def: ast.AST) -> bool:
    for node in ast.walk(func_def):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _COOKIE_ATTRS:
                return True
            if (getattr(func, "id", None) or getattr(func, "attr", None)) in _SESSION_CALLS:
                return True
        # session["k"] = v   /   request.session["k"] = v
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            target = node.value
            if (isinstance(target, ast.Name) and target.id in _SESSION_NAMES) or (
                isinstance(target, ast.Attribute) and target.attr in _SESSION_NAMES
            ):
                return True
    return False


def file_uses_session_middleware(tree: ast.AST) -> bool:
    """True when the module installs server-side session middleware (app-level cookie use)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if any(isinstance(a, ast.Name) and a.id == "SessionMiddleware" for a in node.args):
                return True
            if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == "SessionMiddleware":
                return True
    return False


def handler_uses_sse(func_def: ast.AST, decorator: ast.AST | None = None) -> bool:
    """True when the handler (or its route decorator) streams ``text/event-stream``."""
    for root in (func_def, decorator):
        if root is None:
            continue
        for node in ast.walk(root):
            if isinstance(node, ast.Constant) and node.value == _SSE_MEDIA_TYPE:
                return True
            if isinstance(node, ast.Call) and (
                getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            ) == "EventSourceResponse":
                return True
    return False


def python_handler_signals(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    session_middleware: bool = False,
) -> dict[str, Any]:
    """Evidence dict for ``metadata["handler_signals"]`` (keys always present except injection)."""
    called = _called_names(func_def)
    signals: dict[str, Any] = {
        "audit_logged": any(_AUDIT_RE.search(name) for name in called)
        or any(
            isinstance(n, ast.Attribute) and _AUDIT_RE.search(n.attr) for n in ast.walk(func_def)
        ),
        "sets_cookie": session_middleware or _sets_cookie(func_def),
        "auth_checked_in_handler": any(_AUTH_CHECK_RE.search(name) for name in called),
    }
    injection = _identity_injection(func_def)
    if injection:
        signals["identity_injection"] = injection
    return signals
