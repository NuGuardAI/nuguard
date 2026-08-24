"""NestJS framework adapter — discovers routes, auth guards, and request DTOs.

Registers as a ``TSFrameworkAdapter`` for TypeScript files that import
``@nestjs/common``/``@nestjs/core``. NestJS's decorator-based routing
(``@Controller('prefix')`` + ``@Get()``/``@Post()``/...) has no equivalent to
Python AST's structured decorator nodes exposed by ``ts_parser`` (decorator
*string arguments* aren't captured there — only object-literal ones), so this
adapter scans source text directly with regex, mirroring the structure of
``fastapi_adapter.py``:

- ``@Controller('prefix')`` class + ``@Get/@Post/@Put/@Patch/@Delete('path')``
  method decorators → API_ENDPOINT nodes, prefix + path composed
- ``@UseGuards(...)`` (class- or method-level) → ``auth_type='guard'``;
  ``@Public()`` on a method overrides an outer class-level guard
- ``@Body() dto: SomeDto`` parameter, resolved against a cross-file DTO
  field-type index (populated by ``extractor/core.py``'s pre-pass, the same
  ``set_global_model_schemas`` hook FastAPI/Flask already use) → known
  prompt/chat-history field names → ``chat_payload_key``/``chat_payload_list``
"""

from __future__ import annotations

import re
from typing import Any

from ...types import ComponentType
from ..base import ComponentDetection
from ._class_scan import _CLASS_RE, _find_class_body_span
from ._ts_regex import TSFrameworkAdapter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTTP_METHODS = {"Get", "Post", "Put", "Patch", "Delete"}

# Mirrors nuguard/sbom/adapters/python/fastapi_adapter.py — keep in sync.
_PROMPT_FIELD_NAMES = {
    "message", "query", "prompt", "input", "text",
    "user_query", "user_input", "user_message",
    "transcript", "question", "content", "msg",
}

_MESSAGE_HISTORY_FIELD_NAMES = {"messages", "history", "conversation", "chat_history"}

_NON_CHAT_PAYLOAD_KEYS = frozenset({
    "from_account_id", "to_account_id", "amount", "card_id", "account_id",
    "patient_id", "order_id", "booking_reference", "flight_number",
    "user_id", "transaction_id", "payment_id", "notification_id",
    "recipient_account", "source_account", "debit_account", "credit_account",
})

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

_CONFIDENCE = 0.85

_NESTJS_PACKAGES = ["@nestjs/common", "@nestjs/core"]

_CONTROLLER_RE = re.compile(r"@Controller\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)")
_ROUTE_DECORATOR_RE = re.compile(
    r"@(Get|Post|Put|Patch|Delete)\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)"
)
_METHOD_DEF_RE = re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?(\w+)\s*\(")
_USEGUARDS_RE = re.compile(r"@UseGuards\(")
_PUBLIC_RE = re.compile(r"@Public\(\)")
_BODY_PARAM_RE = re.compile(r"@Body\(\)\s*\w+\s*:\s*([\w][\w.<>\[\]]*)")

# How far past a route decorator to look for the method signature / @Body() /
# @Public() — Swagger decorators (@ApiOperation, @ApiResponse, ...) commonly
# sit between the route decorator and the method line.
_LOOKAHEAD_LINES = 20

# TS interface/class property declarations: `name: Type;` or `name?: Type;`
_DTO_FIELD_RE = re.compile(r"^\s*(\w+)\??\s*:\s*([\w][\w.<>\[\], |]*?)\s*[;,]?\s*$")


def _infer_chat_payload_key(fields: dict[str, str]) -> tuple[str | None, bool]:
    for name in _PROMPT_FIELD_NAMES:
        if name in fields:
            type_str = fields[name]
            is_list = "[]" in type_str or "array" in type_str.lower() or "list" in type_str.lower()
            return name, is_list
    for name in _MESSAGE_HISTORY_FIELD_NAMES:
        if name in fields:
            type_str = fields[name].lower()
            is_array = "[]" in type_str or "array" in type_str
            is_str_array = "string[]" in type_str.replace(" ", "")
            if is_array and not is_str_array:
                return name, True
    for name, type_str in fields.items():
        if type_str.strip().lower() == "string":
            return name, False
    return None, False


def _infer_context_payload_fields(fields: dict[str, str], chat_key: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    lower_identity = {f.lower() for f in _IDENTITY_FIELD_NAMES}
    lower_session = {f.lower() for f in _SESSION_FIELD_NAMES}
    for name in fields:
        if name == chat_key:
            continue
        nl = name.lower()
        if name in _IDENTITY_FIELD_NAMES or nl in lower_identity:
            result[name] = "identity"
        elif name in _SESSION_FIELD_NAMES or nl in lower_session:
            result[name] = "session"
    return result


def collect_dto_schemas(content: str) -> dict[str, dict[str, str]]:
    """Return ``{type_name: {field_name: type_str}}`` for TS ``interface``/``class`` bodies.

    Regex-based, single-file scope — used both locally (same-file DTOs) and by
    ``extractor/core.py``'s cross-file pre-pass so a DTO declared in one module
    (e.g. ``chat.service.ts``) can be resolved when referenced by a
    ``@Body()`` parameter in a sibling controller file.
    """
    schemas: dict[str, dict[str, str]] = {}
    lines = content.splitlines()
    header_re = re.compile(r"\b(?:export\s+)?(?:interface|class)\s+(\w+)")
    i = 0
    n = len(lines)
    while i < n:
        m = header_re.search(lines[i])
        if not m:
            i += 1
            continue
        type_name = m.group(1)
        # Find the opening brace (usually same line, occasionally next line).
        start = i
        brace_line = i
        while brace_line < n and "{" not in lines[brace_line]:
            brace_line += 1
            if brace_line - start > 3:
                break
        if brace_line >= n or "{" not in lines[brace_line]:
            i += 1
            continue
        depth = lines[brace_line].count("{") - lines[brace_line].count("}")
        j = brace_line + 1
        fields: dict[str, str] = {}
        while j < n and depth > 0:
            line = lines[j]
            depth += line.count("{") - line.count("}")
            fm = _DTO_FIELD_RE.match(line)
            if fm and depth >= 1:
                fname, ftype = fm.group(1), fm.group(2).strip()
                if fname not in ("constructor",):
                    fields[fname] = ftype
            j += 1
        if fields:
            schemas[type_name] = fields
        i = j if j > i else i + 1
    return schemas


def _compose_path(prefix: str, route: str, global_prefix: str = "") -> str:
    prefix = (prefix or "").strip("/")
    route = (route or "").strip("/")
    global_prefix = (global_prefix or "").strip("/")
    parts = [p for p in (global_prefix, prefix, route) if p]
    return "/" + "/".join(parts) if parts else ""


# Matches `app.setGlobalPrefix('api/v1')` / `app.setGlobalPrefix("api/v1", {...})`
# as well as the equally common `app.setGlobalPrefix(apiPrefix)` variable form
# (see `_resolve_prefix_variable`) in main.ts (or wherever NestFactory.create's
# entrypoint lives) — Nest applies this prefix to every route in the app,
# outside each controller's own `@Controller('prefix')`.
_GLOBAL_PREFIX_CALL_RE = re.compile(
    r"\.setGlobalPrefix\(\s*(?:['\"]([^'\"]+)['\"]|(\w+))\s*[,)]"
)
_QUOTED_STRING_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def _resolve_prefix_variable(content: str, var_name: str) -> str | None:
    """Resolve ``setGlobalPrefix(apiPrefix)``'s value from its assignment.

    Handles both a plain literal (``const apiPrefix = 'api/v1';``) and the
    common ``ConfigService.get(KEY, DEFAULT)`` pattern (``const apiPrefix =
    configService.get<string>('API_PREFIX', 'api/v1');``) by taking the
    *last* quoted string literal on the assignment's right-hand side (the
    default value, when there's a preceding config-key string too).
    """
    m = re.search(rf"\b{re.escape(var_name)}\s*=\s*([^;\n]+)", content)
    if not m:
        return None
    quoted = _QUOTED_STRING_RE.findall(m.group(1))
    return quoted[-1] if quoted else None


def _extract_global_prefix(content: str) -> tuple[str, list[str]] | None:
    """Return ``(prefix, exclude_patterns)`` from an ``app.setGlobalPrefix(...)``
    call in *content*, or ``None`` if absent.

    ``exclude_patterns`` is a best-effort list of any quoted string literals
    found inside a trailing ``{ exclude: [...] }`` options object (route
    strings or ``{ path: '...', method: ... }`` object literals) — path-shape
    matching only, not full glob semantics.
    """
    m = _GLOBAL_PREFIX_CALL_RE.search(content)
    if not m:
        return None
    literal, var_name = m.group(1), m.group(2)
    if literal is not None:
        prefix = literal
    else:
        resolved = _resolve_prefix_variable(content, var_name)
        if resolved is None:
            return None
        prefix = resolved
    prefix = prefix.strip("/")
    exclude: list[str] = []
    tail = content[m.end() : m.end() + 500]
    ex_m = re.search(r"exclude\s*:\s*\[", tail)
    if ex_m:
        start = ex_m.end()
        depth = 1
        end = len(tail)
        for i, ch in enumerate(tail[start:], start=start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        exclude = _QUOTED_STRING_RE.findall(tail[start:end])
    return prefix, exclude


class NestJSAdapter(TSFrameworkAdapter):
    """Detects NestJS controllers, routes, guards, and request DTOs via regex."""

    name = "nestjs"
    priority = 50
    handles_imports = _NESTJS_PACKAGES

    def __init__(self) -> None:
        self._global_dto_schemas: dict[str, dict[str, str]] = {}
        self._global_route_prefix: str = ""
        self._global_route_prefix_exclude: list[str] = []

    def set_global_model_schemas(self, schemas: dict[str, dict[str, str]]) -> None:
        """Provide cross-file DTO field-type definitions (shared hook name
        with FastAPI/Flask's Pydantic-model index — see extractor/core.py)."""
        self._global_dto_schemas = schemas

    def set_global_route_prefix(self, prefix: str, exclude: list[str] | None = None) -> None:
        """Provide the app-wide route prefix from ``app.setGlobalPrefix(...)``
        in ``main.ts``, applied outside every controller's own
        ``@Controller('prefix')`` — see extractor/core.py's main.ts pre-pass."""
        self._global_route_prefix = (prefix or "").strip("/")
        self._global_route_prefix_exclude = exclude or []

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if not content or not content.strip():
            return []

        lines = content.splitlines()
        local_dto_schemas = collect_dto_schemas(content)
        effective_dto_schemas = {**self._global_dto_schemas, **local_dto_schemas}

        detections: list[ComponentDetection] = []

        for idx, line in enumerate(lines):
            cm = _CONTROLLER_RE.search(line)
            if not cm:
                continue
            prefix = cm.group(1) or ""

            # Scan forward for the `class X` line (possibly preceded by other
            # class-level decorators like @UseGuards, @ApiTags, @ApiBearerAuth).
            class_idx: int | None = None
            class_level_guard = False
            for k in range(idx, min(idx + 10, len(lines))):
                if _USEGUARDS_RE.search(lines[k]):
                    class_level_guard = True
                cls_m = _CLASS_RE.search(lines[k])
                if cls_m:
                    class_idx = k
                    break
            if class_idx is None:
                continue

            body_start, body_end = _find_class_body_span(lines, class_idx)

            k = body_start
            while k < body_end:
                rm = _ROUTE_DECORATOR_RE.search(lines[k])
                if rm is None:
                    k += 1
                    continue

                http_method = rm.group(1)
                route_path = rm.group(2) or ""

                # Bound the lookahead at the next route decorator (or the class
                # body end) so a GET/DELETE handler with no @Body() of its own
                # never picks up the following handler's DTO/guard/@Public().
                next_route_idx = window_end_bound = min(k + _LOOKAHEAD_LINES, body_end)
                for nk in range(k + 1, window_end_bound):
                    if _ROUTE_DECORATOR_RE.search(lines[nk]):
                        next_route_idx = nk
                        break
                window_end = next_route_idx
                window = lines[k:window_end]

                # Decorators on a method can stack in either order above it
                # (e.g. @Public() above @Post(...), or below) — also scan
                # backward from the route decorator, stopping at the first
                # non-decorator/blank line (the previous method's body/brace).
                back_window: list[str] = []
                bk = k - 1
                while bk >= body_start:
                    stripped_bk = lines[bk].strip()
                    if not stripped_bk or stripped_bk.startswith("@"):
                        back_window.append(lines[bk])
                        bk -= 1
                        continue
                    break

                method_is_public = any(_PUBLIC_RE.search(wl) for wl in window + back_window)
                method_has_guard = any(_USEGUARDS_RE.search(wl) for wl in window + back_window)

                dto_type: str | None = None
                if http_method in ("Post", "Put", "Patch"):
                    for wl in window:
                        bm = _BODY_PARAM_RE.search(wl)
                        if bm:
                            dto_type = bm.group(1).strip()
                            break

                # Find the actual method name (first non-decorator line in the window).
                func_name = f"{http_method.lower()}_{k}"
                for wl in window[1:]:
                    stripped = wl.strip()
                    if not stripped or stripped.startswith("@"):
                        continue
                    mdm = _METHOD_DEF_RE.match(wl)
                    if mdm:
                        func_name = mdm.group(1)
                        break
                    break

                composed_path = _compose_path(prefix, route_path)
                if self._global_route_prefix:
                    bare_path = (composed_path or "").strip("/")
                    excluded = any(
                        pat.strip("/") and pat.strip("/") in bare_path
                        for pat in self._global_route_prefix_exclude
                    )
                    if not excluded:
                        composed_path = _compose_path(
                            prefix, route_path, self._global_route_prefix
                        )
                canon = (
                    f"endpoint:{http_method.upper()}:{composed_path}"
                    if composed_path
                    else f"endpoint:{http_method.upper()}::{file_path}"
                )

                auth_required = (class_level_guard or method_has_guard) and not method_is_public

                schema: dict[str, str] = {}
                chat_key: str | None = None
                chat_list = False
                ctx_fields: dict[str, str] = {}
                if dto_type:
                    # Strip generic wrappers / array markers to get the bare type name.
                    bare_type = re.sub(r"\[\]$", "", dto_type).strip()
                    schema = effective_dto_schemas.get(bare_type, {})
                    if schema:
                        chat_key, chat_list = _infer_chat_payload_key(schema)
                        ctx_fields = _infer_context_payload_fields(schema, chat_key)

                metadata: dict[str, Any] = {
                    "framework": "nestjs",
                    "method": http_method.upper(),
                }
                if composed_path is not None:
                    metadata["endpoint"] = composed_path
                if class_level_guard or method_has_guard:
                    metadata["auth_type"] = "guard"
                metadata["auth_required"] = auth_required
                if schema:
                    metadata["request_body_schema"] = schema
                if chat_key and chat_key not in _NON_CHAT_PAYLOAD_KEYS:
                    metadata["chat_payload_key"] = chat_key
                    metadata["chat_payload_list"] = chat_list
                if ctx_fields:
                    metadata["context_payload_fields"] = ctx_fields

                ep_display = re.sub(r"([a-z])([A-Z])", r"\1 \2", func_name).title()

                ep_detection = ComponentDetection(
                    component_type=ComponentType.API_ENDPOINT,
                    canonical_name=canon,
                    display_name=ep_display,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata=metadata,
                    file_path=file_path,
                    line=k + 1,
                    snippet=f"@{http_method}({route_path!r})",
                    evidence_kind="regex",
                )
                detections.append(ep_detection)

                k += 1

        return detections
