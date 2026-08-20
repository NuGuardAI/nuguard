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
from ..base import ComponentDetection, RelationshipHint
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
_CLASS_RE = re.compile(r"\bclass\s+(\w+)")
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


def _find_class_body_span(lines: list[str], class_line_idx: int) -> tuple[int, int]:
    """Return ``(body_start, body_end)`` line indices (inclusive) for the class
    whose ``class X`` declaration is on ``class_line_idx``, via brace counting."""
    n = len(lines)
    brace_line = class_line_idx
    while brace_line < n and "{" not in lines[brace_line]:
        brace_line += 1
        if brace_line - class_line_idx > 5:
            return class_line_idx, class_line_idx
    if brace_line >= n:
        return class_line_idx, class_line_idx
    depth = lines[brace_line].count("{") - lines[brace_line].count("}")
    j = brace_line + 1
    while j < n and depth > 0:
        depth += lines[j].count("{") - lines[j].count("}")
        j += 1
    return brace_line, min(j, n - 1)


def _compose_path(prefix: str, route: str) -> str:
    prefix = (prefix or "").strip("/")
    route = (route or "").strip("/")
    parts = [p for p in (prefix, route) if p]
    return "/" + "/".join(parts) if parts else ""


class NestJSAdapter(TSFrameworkAdapter):
    """Detects NestJS controllers, routes, guards, and request DTOs via regex."""

    name = "nestjs"
    priority = 50
    handles_imports = _NESTJS_PACKAGES

    def __init__(self) -> None:
        self._global_dto_schemas: dict[str, dict[str, str]] = {}

    def set_global_model_schemas(self, schemas: dict[str, dict[str, str]]) -> None:
        """Provide cross-file DTO field-type definitions (shared hook name
        with FastAPI/Flask's Pydantic-model index — see extractor/core.py)."""
        self._global_dto_schemas = schemas

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

            controller_name = _CLASS_RE.search(lines[class_idx]).group(1)  # type: ignore[union-attr]
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

                fw_canon = f"nestjs:framework:{file_path}:{controller_name}"
                ep_detection.relationships.append(RelationshipHint(
                    source_canonical=fw_canon,
                    source_type=ComponentType.FRAMEWORK,
                    target_canonical=canon,
                    target_type=ComponentType.API_ENDPOINT,
                    relationship_type="CALLS",
                ))

                k += 1

            detections.append(ComponentDetection(
                component_type=ComponentType.FRAMEWORK,
                canonical_name=f"nestjs:framework:{file_path}:{controller_name}",
                display_name=controller_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=_CONFIDENCE,
                metadata={"framework": "nestjs", "class": "Controller"},
                file_path=file_path,
                line=class_idx + 1,
                evidence_kind="regex",
            ))

        return detections
