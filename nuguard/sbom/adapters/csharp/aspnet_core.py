"""C# adapter for ASP.NET Core controller and Minimal API routes."""

from __future__ import annotations

import re
from typing import Any

from ...core.csharp_parser import parse_csharp
from ...http_semantics import HANDLER_SIGNALS_KEY
from ...models import HttpParameterLocation, HttpParameterMetadata, HttpRequestMetadata
from ...normalization import canonicalize_text
from ...types import ComponentType
from .._handler_signals_text import regex_handler_signals
from ..base import ComponentDetection, RelationshipHint
from ._call_graph import (
    AGENT_HOST_FILES_KEY,
    CallGraph,
    empty_call_graph,
    enclosing_type,
    handler_agent_files,
)
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

# Handler resolves the caller from the credential rather than the request.
_CSHARP_IDENTITY_PATTERNS = (
    re.compile(r"\bClaimsPrincipal\s+\w+|\bICurrentUser\w*\s+\w+|\bIHttpContextAccessor\b"),
    re.compile(r"\bUser\.(?:Identity|FindFirst|Claims)\b|\bHttpContext\.User\b"),
)
_CSHARP_COOKIE_PATTERNS = (
    re.compile(r"\bResponse\.Cookies\.Append\s*\(|\bCookieOptions\b|\bHttpContext\.Session\b"),
    re.compile(r"\bSignInAsync\s*\("),
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
# OpenAI-style chat-history field names — see fastapi_adapter._MESSAGE_HISTORY_FIELD_NAMES.
_MESSAGE_HISTORY_FIELDS = {"messages", "history", "conversation", "chat_history"}
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
_BINDING_SOURCES = {
    "AsParameters": "parameters",
    "FromBody": "body",
    "FromForm": "form",
    "FromHeader": "header",
    "FromKeyedServices": "services",
    "FromQuery": "query",
    "FromRoute": "route",
    "FromServices": "services",
}
_PROPERTY_RE = re.compile(
    r"(?m)^\s*public\s+"
    r"(?P<type>[A-Za-z_][\w.<>,?\[\]]*)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*"
    r"\{\s*(?:get|init|set)\s*;"
)


class CSharpAspNetCoreAdapter(CSharpFrameworkAdapter):
    """Detect controller and Minimal API endpoints."""

    name = "csharp_aspnet_core"
    priority = 50
    handles_namespaces = ["Microsoft.AspNetCore"]

    def __init__(self) -> None:
        # Route-group scopes handed to methods in other files, keyed by method name.
        self._route_group_bindings: dict[str, _RouteScope] = {}
        # Repository-wide method call graph, to link an endpoint to the agents it drives.
        self._call_graph: CallGraph = empty_call_graph()

    def set_csharp_call_graph(self, graph: CallGraph) -> None:
        """Receive the cross-file call graph from the extractor pre-pass."""
        self._call_graph = graph

    def set_csharp_route_group_bindings(
        self,
        bindings: dict[str, _RouteScope],
    ) -> None:
        """Receive cross-file route-group scopes from the extractor pre-pass."""
        self._route_group_bindings = dict(bindings)

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

        detections.extend(
            self._controller_endpoints(
                content,
                file_path,
                result,
                constants,
                root,
            )
        )
        detections.extend(
            self._minimal_endpoints(
                content,
                file_path,
                result,
                constants,
                root,
            )
        )

        if len(detections) == 1 and import_line == 0 and builder_call is None:
            # Only implicit Web SDK usings: not evidence of ASP.NET Core on their own.
            return []

        return _dedupe(detections)

    def _controller_endpoints(
        self,
        content: str,
        file_path: str,
        result: Any,
        constants: dict[str, str],
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
                    http_request=_http_request(
                        list(method.parameters), http_method, route, request_schema
                    ),
                    handler_text="\n".join([*method.parameters, *method.attributes, body]),
                    agent_files=handler_agent_files(
                        self._call_graph,
                        content,
                        result,
                        body,
                        method.containing_type or "",
                        list(method.parameters),
                    ),
                )
            )

        return detections

    def _minimal_endpoints(
        self,
        content: str,
        file_path: str,
        result: Any,
        constants: dict[str, str],
        root: str,
    ) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []
        groups = _route_groups(
            content,
            result,
            constants,
            self._route_group_bindings,
        )

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

            group_prefix, group_requires, group_anonymous = _receiver_scope(
                call.receiver or "",
                call.line,
                groups,
                result,
                constants,
                self._route_group_bindings,
            )

            handler = call.positional_arguments[-1] if len(call.positional_arguments) > 1 else ""

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
            _, requires, anonymous = _fold_chain(
                ("", group_requires, group_anonymous),
                tail,
                constants,
                include_groups=False,
            )
            attributes = re.match(r"\s*(?:\[[^\]]*\]\s*)*", mask_non_code(handler))
            names = re.findall(
                r"[\[,]\s*(?:Microsoft\.AspNetCore\.Authorization\.)?"
                r"(Authorize|AllowAnonymous)(?:Attribute)?\b",
                attributes.group(0) if attributes else "",
            )
            requires = requires or "Authorize" in names
            anonymous = anonymous or "AllowAnonymous" in names

            # AllowAnonymous metadata wins over RequireAuthorization at any level.
            auth_required = requires and not anonymous
            response_type = _minimal_response_type(handler)
            response_schema = _schema_for_type(
                content,
                result,
                response_type,
            )
            response_key = _response_key(response_schema)
            normalized_route = _join_route(group_prefix, route)
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
                    http_request=_http_request(
                        list(_lambda_parameters(handler)), method, normalized_route, request_schema
                    ),
                    handler_text=handler,
                    agent_files=handler_agent_files(
                        self._call_graph,
                        content,
                        result,
                        handler,
                        enclosing_type(result, call.line),
                        list(_lambda_parameters(handler)),
                    ),
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
    http_request: dict[str, Any] | None = None,
    handler_text: str | None = None,
    agent_files: list[str] | None = None,
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

    if agent_files:
        # Consumed (and removed) by the extractor, which links the endpoint to the
        # AGENT nodes detected in these files.
        metadata[AGENT_HOST_FILES_KEY] = list(agent_files)

    if http_request:
        metadata["http_request"] = http_request

    if handler_text is not None:
        metadata[HANDLER_SIGNALS_KEY] = regex_handler_signals(
            handler_text,
            identity_patterns=_CSHARP_IDENTITY_PATTERNS,
            cookie_patterns=_CSHARP_COOKIE_PATTERNS,
        )

    if response_schema:
        metadata["response_body_schema"] = response_schema

    if chat_key:
        metadata["chat_payload_key"] = chat_key
        # No type introspection available from the regex-based C# extraction —
        # a name match against the OpenAI-style history field set is treated as
        # sufficient signal, same heuristic as the Flask adapter.
        metadata["chat_payload_list"] = chat_key.lower() in _MESSAGE_HISTORY_FIELDS

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


# (prefix, RequireAuthorization seen, AllowAnonymous seen) for a route builder.
_RouteScope = tuple[str, bool, bool]
_EMPTY_SCOPE: _RouteScope = ("", False, False)
_GROUP_CALLS = {"MapGroup", "RequireAuthorization", "AllowAnonymous"}
_ROUTE_BUILDER_PARAM_RE = re.compile(
    r"^\s*(?:this\s+)?(?:[\w.]+\.)?"
    r"(?:IEndpointRouteBuilder|RouteGroupBuilder|WebApplication)\??\s+"
    r"@?(?P<name>[A-Za-z_]\w*)\s*$"
)
_ROOT_IDENTIFIER_RE = re.compile(r"\s*@?(?P<name>[A-Za-z_]\w*)")


def _join_route(prefix: str, route: str) -> str:
    if not prefix.strip("/"):
        return _normalize_route(route)

    return _normalize_route(f"{prefix.rstrip('/')}/{route.lstrip('/')}")


def _fold_chain(
    scope: _RouteScope,
    chain: str,
    constants: dict[str, str],
    include_groups: bool = True,
) -> _RouteScope:
    """Apply MapGroup / RequireAuthorization / AllowAnonymous calls in *chain*."""
    prefix, requires, anonymous = scope

    for call in sorted(find_calls(chain, _GROUP_CALLS), key=lambda item: item.start):
        if call.name == "MapGroup":
            if include_groups and call.positional_arguments:
                group = resolve_expression(call.positional_arguments[0], constants)
                prefix = _join_route(prefix, group or "")
        elif call.name == "RequireAuthorization":
            requires = True
        else:
            anonymous = True

    return prefix, requires, anonymous


def _parameter_scope(
    name: str,
    line: int,
    result: Any,
    bindings: dict[str, _RouteScope],
) -> _RouteScope | None:
    """Return the scope bound to a route-builder parameter of the enclosing method."""
    for method in result.method_declarations:
        if not method.line <= line <= max(method.line_end, method.line):
            continue

        for parameter in method.parameters:
            match = _ROUTE_BUILDER_PARAM_RE.match(parameter)

            if match is not None and match.group("name") == name:
                qualified = bindings.get(f"{method.containing_type}.{method.name}")
                return (
                    qualified if qualified is not None else bindings.get(method.name, _EMPTY_SCOPE)
                )

    return None


def _receiver_scope(
    receiver: str,
    line: int,
    groups: dict[str, _RouteScope],
    result: Any,
    constants: dict[str, str],
    bindings: dict[str, _RouteScope],
) -> _RouteScope:
    """Resolve the route scope of a builder expression such as ``g`` or ``app.MapGroup("/x")``."""
    match = _ROOT_IDENTIFIER_RE.match(receiver)

    if match is None:
        return _EMPTY_SCOPE

    root = match.group("name")
    scope = groups.get(root) or _parameter_scope(root, line, result, bindings) or _EMPTY_SCOPE

    return _fold_chain(scope, receiver[match.end() :], constants)


def _route_groups(
    content: str,
    result: Any,
    constants: dict[str, str],
    bindings: dict[str, _RouteScope],
) -> dict[str, _RouteScope]:
    """Return route-group variables (``var g = app.MapGroup(...)``) and their scopes."""
    definitions: dict[str, tuple[str | None, _RouteScope, _RouteScope]] = {}

    if "MapGroup" not in content:
        return {}

    def resolve(name: str, seen: frozenset[str] = frozenset()) -> _RouteScope:
        parent, base, own = definitions[name]

        if parent is not None and parent not in seen:
            base = resolve(parent, seen | {name})

        prefix = _join_route(base[0], own[0]) if own[0].strip("/") else base[0]
        return prefix, base[1] or own[1], base[2] or own[2]

    for call in sorted(find_calls(content, _GROUP_CALLS), key=lambda item: item.start):
        if call.name == "MapGroup" and call.assigned_to:
            chain = content[call.start : call.end] + statement_tail(content, call.end)
            match = _ROOT_IDENTIFIER_RE.match(chain)

            if match is None:
                continue

            root = match.group("name")
            parent: str | None = None

            if root == call.assigned_to and root in definitions:
                base = resolve(root)
            elif root in definitions:
                parent, base = root, _EMPTY_SCOPE
            else:
                base = _parameter_scope(root, call.line, result, bindings) or _EMPTY_SCOPE

            own = _fold_chain(_EMPTY_SCOPE, chain[match.end() :], constants)
            definitions[call.assigned_to] = (parent, base, own)
        elif call.name != "MapGroup":
            # A later standalone ``g.RequireAuthorization().AllowAnonymous();`` statement.
            match = _ROOT_IDENTIFIER_RE.match(call.receiver or "")

            if match is None or match.group("name") not in definitions:
                continue

            rest = (call.receiver or "")[match.end() :]

            if rest.strip() and (
                not rest.lstrip().startswith(".")
                or any(item.name.startswith("Map") for item in find_calls(rest))
            ):
                continue

            parent, base, own = definitions[match.group("name")]
            definitions[match.group("name")] = (
                parent,
                base,
                _fold_chain(own, f".{call.name}()", constants),
            )

    return {name: resolve(name) for name in definitions}


def collect_route_group_bindings(
    content: str,
    file_path: str = "",
) -> dict[str, _RouteScope]:
    """Return route-group scopes passed to other methods, keyed by callee name and ``Type.Method``.

    Covers ``ChatEndpoints.Map(group)`` and extension methods such as
    ``group.MapChatAdapter()``, so endpoints registered in another file inherit
    the group's prefix and authorization.
    """
    if "MapGroup" not in content:
        return {}

    result = parse_csharp(content, file_path)
    constants = string_constants(result)
    groups = _route_groups(content, result, constants, {})
    bindings: dict[str, _RouteScope] = {}
    ambiguous: set[str] = set()

    def bind(key: str, scope: _RouteScope) -> None:
        if key in ambiguous:
            return

        if bindings.setdefault(key, scope) != scope:
            ambiguous.add(key)
            del bindings[key]

    for call in find_calls(content):
        if call.name in _GROUP_CALLS or call.name in _MAP_METHODS:
            continue

        if (call.receiver or "") in groups:
            bind(call.name, groups[call.receiver or ""])

        declaring_type = (call.receiver or "").rstrip("?").split(".")[-1]

        for argument in call.positional_arguments:
            if argument.strip() in groups:
                bind(call.name, groups[argument.strip()])

                if declaring_type:
                    bind(f"{declaring_type}.{call.name}", groups[argument.strip()])

    return bindings


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
    """Return an outer C# lambda's parameters using balanced delimiters."""
    masked = mask_non_code(handler)
    arrow = masked.find("=>")

    if arrow >= 0:
        cursor = arrow

        while cursor > 0 and masked[cursor - 1].isspace():
            cursor -= 1

        if cursor > 0 and masked[cursor - 1] == ")":
            close_paren = cursor - 1
            open_paren = _matching_lambda_open_paren(
                masked,
                close_paren,
            )

            if open_paren is not None:
                _, positional = parse_arguments(handler[open_paren + 1 : close_paren])
                return positional

    single = re.search(
        r"(?:async\s+)?"
        r"(?P<param>[A-Za-z_]\w*)"
        r"\s*=>",
        masked,
    )

    return [handler[single.start("param") : single.end("param")]] if single else []


def _matching_lambda_open_paren(
    source: str,
    close_paren: int,
) -> int | None:
    """Return the opening parenthesis paired with *close_paren*."""
    depth = 0

    for index in range(
        close_paren,
        -1,
        -1,
    ):
        char = source[index]

        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1

            if depth == 0:
                return index

    return None


def _parse_parameter(
    value: str,
) -> tuple[str, str, str | None] | None:
    binding_source = _binding_source(value)
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
        binding_source,
    )


def _binding_source(
    value: str,
) -> str | None:
    """Return the explicit ASP.NET parameter binding source, if any."""
    for section in re.findall(
        r"\[([^\]]+)\]",
        value,
    ):
        normalized = re.sub(
            r"^\s*(?:assembly|event|field|method|module|"
            r"param|property|return|type|typevar)"
            r"\s*:\s*",
            "",
            section,
            count=1,
            flags=re.IGNORECASE,
        )

        for attribute in split_top_level(normalized):
            name = attribute.split("(", 1)[0].strip().split(".")[-1].removesuffix("Attribute")
            binding = _BINDING_SOURCES.get(name)

            if binding is not None:
                return binding

    return None


_BINDING_LOCATIONS: dict[str, HttpParameterLocation] = {
    "query": "query",
    "route": "path",
    "header": "header",
    "form": "form",
}
_BINDING_NAME_RE = re.compile(
    r"From(?:Query|Route|Header|Form)(?:Attribute)?\s*\(\s*Name\s*=\s*\"([^\"]+)\""
)


def _type_hint(type_name: str) -> str:
    base = _base_type(type_name)
    if _is_collection_type(type_name):
        return "list"
    if base in {"int", "long", "short", "uint", "ulong", "ushort", "byte"}:
        return "int"
    if base in {"double", "float", "decimal"}:
        return "float"
    if base == "bool":
        return "bool"
    return "string"


def _http_request(
    raw_parameters: list[str],
    http_method: str,
    route: str,
    request_schema: dict[str, str],
) -> dict[str, Any]:
    """Technology-neutral request shape from ASP.NET Core parameter bindings.

    Explicit ``[FromQuery]``/``[FromRoute]``/``[FromHeader]``/``[FromForm]``
    bindings map to their location (honoring ``Name = "..."``); unbound
    primitives follow ASP.NET's own rules (route template name -> path,
    otherwise query); a complex body contributes its resolved fields as JSON
    inputs, or marks the inputs unresolved when its type isn't visible.
    """
    route_names = {name.casefold() for name in re.findall(r"\{(\w+)", route)}
    parameters: dict[tuple[str, str], HttpParameterMetadata] = {}
    content_types: list[str] = []
    unresolved = False

    def add(name: str, location: HttpParameterLocation, type_name: str, required: bool) -> None:
        if name and (name, location) not in parameters:
            parameters[(name, location)] = HttpParameterMetadata(
                name=name, location=location, type_hint=_type_hint(type_name), required=required
            )

    for raw in raw_parameters:
        parsed = _parse_parameter(raw)
        if parsed is None:
            continue
        type_name, name, binding = parsed
        name = name.lstrip("@")
        base = _base_type(type_name)
        optional = type_name.rstrip().endswith("?") or "=" in re.sub(r"\[[^\]]*\]", "", raw)
        named = _BINDING_NAME_RE.search(raw)
        wire_name = named.group(1) if named else name

        if base in {"IFormFile", "IFormFileCollection"}:
            add(wire_name, "multipart", type_name, not optional)
            continue
        if binding in {"services", "parameters"} or base in _INFRASTRUCTURE_TYPES:
            unresolved = unresolved or binding == "parameters"
            continue
        if binding in _BINDING_LOCATIONS:
            location = _BINDING_LOCATIONS[binding]
            add(wire_name, location, type_name, location == "path" or not optional)
            continue
        if base in _PRIMITIVE_TYPES and binding is None:
            location = "path" if name.casefold() in route_names else "query"
            add(wire_name, location, type_name, location == "path" or not optional)
            continue
        # [FromBody] or an implicitly body-bound complex type.
        if request_schema:
            for field, field_type in request_schema.items():
                add(field, "json", field_type, False)
        else:
            unresolved = True
        content_types.append("application/json")

    locations = {parameter.location for parameter in parameters.values()}
    if "multipart" in locations:
        content_types.insert(0, "multipart/form-data")
    if "form" in locations:
        content_types.insert(0, "application/x-www-form-urlencoded")

    method = http_method.upper()
    return HttpRequestMetadata(
        methods=[method] if method and method != "ANY" else ["UNKNOWN"],
        parameters=list(parameters.values()),
        content_types=list(dict.fromkeys(content_types)),
        has_unresolved_inputs=unresolved,
    ).model_dump(mode="json")


def _request_parameter(
    parameters: list[tuple[str, str, str | None]],
) -> tuple[str, str | None]:
    ordered = sorted(
        parameters,
        key=lambda item: item[2] != "body",
    )

    for type_name, name, binding in ordered:
        base = _base_type(type_name)

        if binding in {
            "form",
            "header",
            "parameters",
            "query",
            "route",
            "services",
        }:
            continue

        if base in _INFRASTRUCTURE_TYPES:
            continue

        if base in _PRIMITIVE_TYPES:
            if binding == "body":
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


__all__ = ["CSharpAspNetCoreAdapter", "collect_route_group_bindings"]
