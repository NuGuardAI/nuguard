"""C# adapter for AI-facing ASP.NET Core routes."""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._csharp_base import CSharpFrameworkAdapter
from ._source import (
    find_calls,
    mask_non_code,
    method_source,
    parse_arguments,
    resolve_expression,
    split_top_level,
    statement_tail,
    string_constants,
)

_HTTP_ATTRIBUTES = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpDelete": "DELETE",
    "HttpPatch": "PATCH",
}
_MAP_METHODS = {
    "MapGet": "GET",
    "MapPost": "POST",
    "MapPut": "PUT",
    "MapDelete": "DELETE",
    "MapPatch": "PATCH",
}
_PROMPT_FIELDS = {
    "message",
    "messages",
    "prompt",
    "query",
    "question",
    "input",
    "text",
    "userinput",
    "user_input",
}
_RESPONSE_FIELDS = {
    "answer",
    "content",
    "message",
    "output",
    "response",
    "result",
    "text",
}
_PRIMITIVE_TYPES = {
    "bool",
    "byte",
    "char",
    "DateTime",
    "DateTimeOffset",
    "decimal",
    "double",
    "float",
    "Guid",
    "int",
    "long",
    "short",
    "string",
    "uint",
    "ulong",
    "ushort",
}
_INFRASTRUCTURE_TYPES = {
    "CancellationToken",
    "ClaimsPrincipal",
    "HttpContext",
    "HttpRequest",
    "HttpResponse",
    "IFormFile",
    "IHeaderDictionary",
    "IServiceProvider",
}
_AI_ROUTE_RE = re.compile(
    r"(?:^|[/_-])"
    r"(?:ai|ask|assistant|chat|complete|"
    r"completion|generate|inference|model|"
    r"predict|prompt)"
    r"(?:$|[/_-])",
    re.IGNORECASE,
)
_AI_CODE_RE = re.compile(
    r"\b(?:AnthropicClient|AzureOpenAIClient|"
    r"ChatClient|CompleteChat(?:Async)?|"
    r"GetChatClient|Kernel\.(?:CreateBuilder|Invoke)|"
    r"InvokePrompt(?:Async)?|"
    r"Messages\.(?:Create|CreateAsync)|"
    r"MLContext|PredictionEngine|"
    r"OpenAIClient|ResponsesClient)\b"
)
_AI_NAMESPACE_PREFIXES = (
    "Anthropic",
    "Azure.AI.OpenAI",
    "Microsoft.ML",
    "Microsoft.SemanticKernel",
    "OpenAI",
)
_PROPERTY_RE = re.compile(
    r"(?m)^\s*public\s+"
    r"(?P<type>[A-Za-z_][\w.<>,?\[\]]*)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"\{\s*(?:get|init|set)\s*;"
)


class CSharpAspNetCoreAdapter(CSharpFrameworkAdapter):
    """Detect AI-facing controllers and Minimal API endpoints."""

    name = "csharp_aspnet_core"
    priority = 50
    handles_namespaces = ["Microsoft.AspNetCore"]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(
            content,
            file_path,
            parse_result,
        )

        builder_call = next(
            (
                call
                for call in find_calls(
                    content,
                    {"CreateBuilder"},
                )
                if (call.receiver or "").split(".")[-1] == "WebApplication"
            ),
            None,
        )

        if not self._detect(result) and builder_call is None:
            return []

        constants = string_constants(result)
        root = "framework:aspnet_core"
        import_line = next(
            (
                item.line
                for item in result.using_directives
                if item.namespace.startswith("Microsoft.AspNetCore")
            ),
            0,
        )
        detections: list[ComponentDetection] = [
            ComponentDetection(
                component_type=(ComponentType.FRAMEWORK),
                canonical_name=root,
                display_name="ASP.NET Core",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.97,
                metadata={
                    "framework": "aspnet_core",
                    "language": "csharp",
                },
                file_path=file_path,
                line=import_line,
                snippet=("using Microsoft.AspNetCore"),
                evidence_kind="ast_import",
            )
        ]

        ai_imported = any(
            any(
                (item.namespace == prefix or item.namespace.startswith(prefix + "."))
                for prefix in _AI_NAMESPACE_PREFIXES
            )
            for item in result.using_directives
        )

        detections.extend(
            self._controller_endpoints(
                content,
                file_path,
                result,
                constants,
                ai_imported,
                root,
            )
        )
        detections.extend(
            self._minimal_endpoints(
                content,
                file_path,
                result,
                constants,
                ai_imported,
                root,
            )
        )

        return _dedupe(detections)

    def _controller_endpoints(
        self,
        content: str,
        file_path: str,
        result: Any,
        constants: dict[str, str],
        ai_imported: bool,
        root: str,
    ) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []
        controllers = {
            item.name: (
                item,
                _declaration_attributes(
                    item.attributes,
                    item.signature,
                ),
            )
            for item in result.type_declarations
            if (
                any(_base_type(base) == "ControllerBase" for base in item.base_types)
                or _has_attribute(
                    _declaration_attributes(
                        item.attributes,
                        item.signature,
                    ),
                    "ApiController",
                )
            )
        }

        for method in result.method_declarations:
            controller_entry = controllers.get(method.containing_type or "")

            if controller_entry is None:
                continue

            (
                controller,
                controller_attributes,
            ) = controller_entry
            method_attributes = _declaration_attributes(
                method.attributes,
                method.signature,
            )
            http_attribute = _http_attribute(
                method_attributes,
                constants,
            )

            if http_attribute is None:
                continue

            http_method, action_route = http_attribute

            if not action_route:
                action_route = _route_attribute(
                    method_attributes,
                    constants,
                )

            base_route = _route_attribute(
                controller_attributes,
                constants,
            )
            route = _combine_routes(
                base_route,
                action_route,
                controller.name,
                method.name,
            )
            body = method_source(
                content,
                method,
            )

            if not _is_ai_endpoint(
                route,
                method.name,
                body,
                ai_imported,
            ):
                continue

            auth_required = (
                _has_attribute(
                    controller_attributes,
                    "Authorize",
                )
                or _has_attribute(
                    method_attributes,
                    "Authorize",
                )
            ) and not (
                _has_attribute(
                    controller_attributes,
                    "AllowAnonymous",
                )
                or _has_attribute(
                    method_attributes,
                    "AllowAnonymous",
                )
            )

            params = [
                parsed
                for value in method.parameters
                if (parsed := _parse_parameter(value)) is not None
            ]
            (
                request_type,
                request_name,
            ) = _request_parameter(params)
            request_schema = _request_schema(
                content,
                result,
                request_type,
                request_name,
            )
            chat_key = _chat_key(
                request_schema,
                body,
                request_name,
            )
            response_type = _unwrap_return_type(method.return_type or "")
            response_schema = _schema_for_type(
                content,
                result,
                response_type,
            )
            response_key = _response_key(response_schema)

            detections.append(
                _endpoint_node(
                    adapter=self,
                    root=root,
                    file_path=file_path,
                    line=method.line,
                    http_method=http_method,
                    route=route,
                    display_name=method.name,
                    snippet=method.signature,
                    evidence_kind=("ast_attribute"),
                    auth_required=(auth_required),
                    request_schema=(request_schema),
                    response_schema=(response_schema),
                    chat_key=chat_key,
                    response_key=response_key,
                )
            )

        return detections

    def _minimal_endpoints(
        self,
        content: str,
        file_path: str,
        result: Any,
        constants: dict[str, str],
        ai_imported: bool,
        root: str,
    ) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []

        for call in find_calls(
            content,
            set(_MAP_METHODS),
        ):
            if not call.positional_arguments:
                continue

            route = resolve_expression(
                call.positional_arguments[0],
                constants,
            )

            if not route:
                continue

            handler = call.positional_arguments[-1] if len(call.positional_arguments) > 1 else ""

            if not _is_ai_endpoint(
                route,
                call.name,
                handler,
                ai_imported,
            ):
                continue

            params = [
                parsed
                for value in _lambda_parameters(handler)
                if (parsed := _parse_parameter(value)) is not None
            ]
            (
                request_type,
                request_name,
            ) = _request_parameter(params)
            request_schema = _request_schema(
                content,
                result,
                request_type,
                request_name,
            )
            chat_key = _chat_key(
                request_schema,
                handler,
                request_name,
            )
            tail = mask_non_code(
                statement_tail(
                    content,
                    call.end,
                )
            )
            auth_required = "RequireAuthorization" in tail and "AllowAnonymous" not in tail
            response_type = _minimal_response_type(handler)
            response_schema = _schema_for_type(
                content,
                result,
                response_type,
            )
            response_key = _response_key(response_schema)
            normalized_route = _normalize_route(route)
            method = _MAP_METHODS[call.name]

            detections.append(
                _endpoint_node(
                    adapter=self,
                    root=root,
                    file_path=file_path,
                    line=call.line,
                    http_method=method,
                    route=normalized_route,
                    display_name=(f"{method} {normalized_route}"),
                    snippet=call.snippet,
                    evidence_kind="ast_call",
                    auth_required=(auth_required),
                    request_schema=(request_schema),
                    response_schema=(response_schema),
                    chat_key=chat_key,
                    response_key=response_key,
                )
            )

        return detections


def _endpoint_node(
    adapter: CSharpAspNetCoreAdapter,
    root: str,
    file_path: str,
    line: int,
    http_method: str,
    route: str,
    display_name: str,
    snippet: str,
    evidence_kind: str,
    auth_required: bool,
    request_schema: dict[str, str],
    response_schema: dict[str, str],
    chat_key: str | None,
    response_key: str | None,
) -> ComponentDetection:
    canonical = canonicalize_text(f"aspnet:endpoint:{http_method}:{route}")
    metadata: dict[str, Any] = {
        "framework": "aspnet_core",
        "method": http_method,
        "endpoint": route,
        "auth_required": auth_required,
        "language": "csharp",
    }

    if request_schema:
        metadata["request_body_schema"] = request_schema

    if response_schema:
        metadata["response_body_schema"] = response_schema

    if chat_key:
        metadata["chat_payload_key"] = chat_key
        metadata["chat_payload_list"] = _is_collection_type(
            request_schema.get(
                chat_key,
                "",
            )
        )

    if response_key:
        metadata["response_text_key"] = response_key

    return ComponentDetection(
        component_type=(ComponentType.API_ENDPOINT),
        canonical_name=canonical,
        display_name=display_name,
        adapter_name=adapter.name,
        priority=adapter.priority,
        confidence=0.92,
        metadata=metadata,
        file_path=file_path,
        line=line,
        snippet=snippet,
        evidence_kind=evidence_kind,
        relationships=[
            RelationshipHint(
                source_canonical=root,
                source_type=(ComponentType.FRAMEWORK),
                target_canonical=canonical,
                target_type=(ComponentType.API_ENDPOINT),
                relationship_type="CALLS",
            )
        ],
    )


def _declaration_attributes(
    parsed: tuple[str, ...],
    signature: str,
) -> tuple[str, ...]:
    """Recover attributes containing brackets inside route strings."""
    recovered: list[str] = []
    index = 0

    while index < len(signature):
        start = signature.find(
            "[",
            index,
        )

        if start < 0:
            break

        depth = 1
        quote: str | None = None
        escaped = False
        cursor = start + 1

        while cursor < len(signature):
            char = signature[cursor]

            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\" and quote == '"':
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in {'"', "'"}:
                quote = char
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1

                if depth == 0:
                    recovered.extend(
                        item.strip()
                        for item in split_top_level(signature[start + 1 : cursor])
                        if item.strip()
                    )
                    cursor += 1
                    break

            cursor += 1

        if depth != 0:
            break

        index = cursor

    return tuple(recovered) if recovered else parsed


def _http_attribute(
    attributes: tuple[str, ...],
    constants: dict[str, str],
) -> tuple[str, str] | None:
    for attribute in attributes:
        name, positional = _attribute_parts(
            attribute,
            constants,
        )

        if name in _HTTP_ATTRIBUTES:
            return (
                _HTTP_ATTRIBUTES[name],
                positional[0] if positional else "",
            )

    return None


def _route_attribute(
    attributes: tuple[str, ...],
    constants: dict[str, str],
) -> str:
    for attribute in attributes:
        name, positional = _attribute_parts(
            attribute,
            constants,
        )

        if name == "Route" and positional:
            return positional[0]

    return ""


def _attribute_parts(
    attribute: str,
    constants: dict[str, str],
) -> tuple[str, list[str]]:
    value = attribute.strip()

    if "(" not in value:
        return (
            value.split(".")[-1].removesuffix("Attribute"),
            [],
        )

    name, raw = value.split(
        "(",
        1,
    )
    raw = raw.rsplit(
        ")",
        1,
    )[0]
    _, positional = parse_arguments(raw)

    return (
        name.split(".")[-1].removesuffix("Attribute"),
        [
            resolved
            for item in positional
            if (
                resolved := resolve_expression(
                    item,
                    constants,
                )
            )
        ],
    )


def _has_attribute(
    attributes: tuple[str, ...],
    expected: str,
) -> bool:
    return any(
        (
            attribute.split(
                "(",
                1,
            )[0]
            .split(".")[-1]
            .removesuffix("Attribute")
            == expected
        )
        for attribute in attributes
    )


def _combine_routes(
    base: str,
    action: str,
    controller_name: str,
    action_name: str,
) -> str:
    controller = controller_name.removesuffix("Controller")
    action_template = action.strip()

    if action_template.startswith("~/"):
        combined = action_template[1:]
    elif action_template.startswith("/"):
        combined = action_template
    else:
        combined = "/".join(part.strip("/") for part in (base, action_template) if part.strip("/"))
    combined = re.sub(
        r"\[controller\]",
        controller,
        combined,
        flags=re.IGNORECASE,
    )
    combined = re.sub(
        r"\[action\]",
        action_name,
        combined,
        flags=re.IGNORECASE,
    )
    return _normalize_route(combined)


def _normalize_route(route: str) -> str:
    clean = re.sub(
        r"/{2,}",
        "/",
        "/" + route.strip(),
    )
    return clean if clean else "/"


def _is_ai_endpoint(
    route: str,
    name: str,
    body: str,
    ai_imported: bool,
) -> bool:
    route_and_name = f"{route}/{name}"

    if _AI_ROUTE_RE.search(route_and_name):
        return True

    code = mask_non_code(body)

    if _AI_CODE_RE.search(code):
        return True

    return ai_imported and bool(
        re.search(
            r"\b(?:client|kernel|"
            r"model|prompt)\b",
            code,
            re.IGNORECASE,
        )
    )


def _minimal_response_type(
    handler: str,
) -> str:
    """Infer a DTO instantiated by a Minimal API handler."""
    code = mask_non_code(handler)
    type_pattern = (
        r"(?P<type>(?:global::)?"
        r"[A-Za-z_]\w*"
        r"(?:\.[A-Za-z_]\w*)*)"
    )
    patterns = (
        (
            r"\breturn\s+"
            r"(?:await\s+)?"
            r"(?:Results\.\w+\s*"
            r"\(\s*)?"
            r"new\s+" + type_pattern + r"\s*\("
        ),
        (
            r"=>\s*"
            r"(?:Results\.\w+\s*"
            r"\(\s*)?"
            r"new\s+" + type_pattern + r"\s*\("
        ),
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            code,
            re.DOTALL,
        )

        if match is not None:
            return _base_type(match.group("type"))

    return ""


def _lambda_parameters(
    handler: str,
) -> list[str]:
    match = re.search(
        r"(?:async\s*)?"
        r"\((?P<params>[^()]*)\)"
        r"\s*=>",
        handler,
        re.DOTALL,
    )

    if match:
        _, positional = parse_arguments(match.group("params"))
        return positional

    single = re.search(
        r"(?:async\s+)?"
        r"(?P<param>[A-Za-z_]\w*)"
        r"\s*=>",
        handler,
    )

    return [single.group("param")] if single else []


def _parse_parameter(
    value: str,
) -> tuple[str, str, bool] | None:
    clean = (
        re.sub(
            r"\[[^\]]+\]\s*",
            "",
            value,
        )
        .split(
            "=",
            1,
        )[0]
        .strip()
    )
    clean = re.sub(
        r"\b(?:in|out|params|ref|"
        r"scoped|this)\s+",
        "",
        clean,
    )
    match = re.search(
        r"(?P<type>"
        r"[A-Za-z_][\w.<>,?\[\]]*)"
        r"\s+"
        r"(?P<name>@?[A-Za-z_]\w*)$",
        clean,
    )

    if not match:
        return None

    return (
        match.group("type"),
        match.group("name").removeprefix("@"),
        "FromBody" in value,
    )


def _request_parameter(
    parameters: list[tuple[str, str, bool]],
) -> tuple[str, str | None]:
    ordered = sorted(
        parameters,
        key=lambda item: not item[2],
    )

    for type_name, name, from_body in ordered:
        base = _base_type(type_name)

        if base in _INFRASTRUCTURE_TYPES:
            continue

        if base in _PRIMITIVE_TYPES:
            if from_body or name.casefold() in _PROMPT_FIELDS:
                return type_name, name

            continue

        if (
            base.startswith("I")
            and len(base) > 1
            and base[1].isupper()
            and not _is_collection_type(type_name)
        ):
            continue

        return type_name, name

    return "", None


def _request_schema(
    content: str,
    result: Any,
    type_name: str,
    parameter_name: str | None,
) -> dict[str, str]:
    schema = _schema_for_type(
        content,
        result,
        type_name,
    )

    if schema or not parameter_name:
        return schema

    base = _base_type(type_name)

    if base in _PRIMITIVE_TYPES or _is_collection_type(type_name):
        return {parameter_name: type_name}

    return {}


def _is_collection_type(
    type_name: str,
) -> bool:
    clean = re.sub(
        r"\s+",
        "",
        type_name,
    ).rstrip("?")

    if clean.endswith("[]"):
        return True

    root = clean.split("<", 1)[0].split(".")[-1]

    return root in {
        "Collection",
        "HashSet",
        "IAsyncEnumerable",
        "ICollection",
        "IEnumerable",
        "IList",
        "IReadOnlyCollection",
        "IReadOnlyList",
        "ImmutableArray",
        "List",
    }


def _schema_for_type(
    content: str,
    result: Any,
    type_name: str,
) -> dict[str, str]:
    if not type_name:
        return {}

    declaration = next(
        (item for item in result.type_declarations if item.name == _base_type(type_name)),
        None,
    )

    if declaration is None:
        return {}

    schema: dict[str, str] = {}
    record_match = re.search(
        r"\((?P<params>[^()]*)\)",
        declaration.signature,
        re.DOTALL,
    )

    if record_match:
        _, values = parse_arguments(record_match.group("params"))

        for value in values:
            parameter = _parse_parameter(value)

            if parameter is not None:
                (
                    field_type,
                    field_name,
                    _,
                ) = parameter
                schema[field_name] = field_type

    lines = content.splitlines()
    start = max(
        declaration.line - 1,
        0,
    )
    end = min(
        max(
            declaration.line_end,
            declaration.line,
        ),
        len(lines),
    )

    for match in _PROPERTY_RE.finditer("\n".join(lines[start:end])):
        schema.setdefault(
            match.group("name"),
            match.group("type"),
        )

    return schema


def _chat_key(
    schema: dict[str, str],
    body: str,
    request_name: str | None,
) -> str | None:
    for field in schema:
        if field.lower() in _PROMPT_FIELDS:
            return field

    if request_name:
        match = re.search(
            rf"\b{re.escape(request_name)}"
            r"\.(?P<field>[A-Za-z_]\w*)",
            body,
        )

        if match and match.group("field").lower() in _PROMPT_FIELDS:
            return match.group("field")

    return None


def _response_key(
    schema: dict[str, str],
) -> str | None:
    return next(
        (field for field in schema if field.lower() in _RESPONSE_FIELDS),
        None,
    )


def _unwrap_return_type(value: str) -> str:
    clean = value.strip().rstrip("?")
    wrappers = (
        "Task",
        "ValueTask",
        "ActionResult",
        "Results",
    )
    changed = True

    while changed:
        changed = False

        for wrapper in wrappers:
            match = re.fullmatch(
                rf"{wrapper}\s*"
                r"<\s*(.+)\s*>",
                clean,
            )

            if match:
                clean = match.group(1).split(",", 1)[0].strip()
                changed = True
                break

    return _base_type(clean)


def _base_type(value: str) -> str:
    clean = value.strip().rstrip("?")
    clean = clean.removeprefix("global::")
    clean = clean.split("<", 1)[0]
    return clean.split(".")[-1]


def _dedupe(
    detections: list[ComponentDetection],
) -> list[ComponentDetection]:
    seen: set[tuple[ComponentType, str]] = set()
    result: list[ComponentDetection] = []

    for detection in detections:
        key = (
            detection.component_type,
            detection.canonical_name,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(detection)

    return result


__all__ = ["CSharpAspNetCoreAdapter"]
