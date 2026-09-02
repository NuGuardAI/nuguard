"""Request-schema and authentication metadata for Go HTTP endpoints."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint

if TYPE_CHECKING:
    from ...core.go_parser import GoFunctionCall, GoParseResult

_PROMPT_FIELD_NAMES = (
    "message",
    "query",
    "prompt",
    "input",
    "text",
    "user_query",
    "user_input",
    "user_message",
    "transcript",
    "question",
    "content",
    "msg",
)
_MESSAGE_HISTORY_FIELD_NAMES = (
    "messages",
    "history",
    "conversation",
    "chat_history",
)

_GIN_MODULE = "github.com/gin-gonic/gin"
_ECHO_JWT_MODULES = {
    "github.com/labstack/echo-jwt",
    "github.com/labstack/echo-jwt/v4",
}
_ECHO_MIDDLEWARE_MODULES = {
    "github.com/labstack/echo/middleware",
    "github.com/labstack/echo/v4/middleware",
}
_GIN_JWT_MODULES = {
    "github.com/appleboy/gin-jwt",
    "github.com/appleboy/gin-jwt/v2",
    "github.com/appleboy/gin-jwt/v3",
}

_BIND_PATTERNS = {
    "gin": re.compile(
        r"\.(?:ShouldBindJSON|BindJSON|ShouldBind|Bind)\s*\(\s*&?\s*"
        r"(?P<variable>[A-Za-z_][A-Za-z0-9_]*)"
    ),
    "echo": re.compile(
        r"\.Bind\s*\(\s*&?\s*"
        r"(?P<variable>[A-Za-z_][A-Za-z0-9_]*)"
    ),
    "net_http": re.compile(
        r"\.Decode\s*\(\s*&?\s*"
        r"(?P<variable>[A-Za-z_][A-Za-z0-9_]*)"
    ),
}
_TYPE_STRUCT_RE = re.compile(
    r"(?m)^\s*type\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s+struct\s*\{"
)
_FUNCTION_RE = re.compile(
    r"(?m)^\s*func\s+"
    r"(?:\([^\n)]*\)\s*)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_FIELD_RE = re.compile(
    r"^(?P<names>[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)"
    r"\s+(?P<type>.+?)\s*$"
)
_JSON_TAG_RE = re.compile(r'(?:^|\s)json:"(?P<value>[^"]*)"')
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HANDLER_WRAPPER_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
    r"HandlerFunc\s*\(\s*"
    r"(?P<handler>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*\)$"
)
_AUTH_DISPLAY_NAMES = {
    "basic": "HTTP Basic Auth",
    "jwt": "JWT Middleware",
}


@dataclass(frozen=True)
class GoHTTPAuthEvidence:
    """One recognized authentication middleware expression."""

    auth_type: str
    line: int
    snippet: str


@dataclass(frozen=True)
class GoHTTPRouteAnalysis:
    """Static request and authentication metadata for one route."""

    request_body_schema: dict[str, str]
    chat_payload_key: str | None
    chat_payload_list: bool
    auth_evidence: tuple[GoHTTPAuthEvidence, ...]

    @property
    def auth_required(self) -> bool:
        """Return whether recognized middleware protects this route."""
        return bool(self.auth_evidence)


def enrich_http_endpoint_detections(
    content: str,
    result: GoParseResult,
    detections: list[ComponentDetection],
    *,
    framework: str,
    verb_names: dict[str, str],
) -> list[ComponentDetection]:
    """Enrich existing endpoint detections with schema and auth evidence."""
    endpoints: dict[tuple[str, str], ComponentDetection] = {}

    for detection in detections:
        if detection.component_type != ComponentType.API_ENDPOINT:
            continue

        method = detection.metadata.get("method")
        path = detection.metadata.get("endpoint")
        if isinstance(method, str) and isinstance(path, str):
            endpoints[(method, path)] = detection

    auth_nodes: dict[str, ComponentDetection] = {}
    analyzed: set[tuple[str, str]] = set()

    for call in result.function_calls:
        method = verb_names.get(call.function_name)
        if method is None:
            continue

        path = _resolved_string_argument(call, 0)
        key = (method, path)
        endpoint = endpoints.get(key)

        if endpoint is None or key in analyzed:
            continue

        analyzed.add(key)
        analysis = analyze_http_route(
            content,
            result,
            call,
            framework=framework,
        )

        endpoint.metadata.update(
            {
                "request_body_schema": (analysis.request_body_schema),
                "chat_payload_key": (analysis.chat_payload_key),
                "chat_payload_list": (analysis.chat_payload_list),
                "auth_required": (analysis.auth_required),
            }
        )

        if analysis.request_body_schema:
            endpoint.metadata["accepts_user_input"] = True

        auth_types = [item.auth_type for item in analysis.auth_evidence]

        if auth_types:
            endpoint.metadata["auth_type"] = auth_types[0]
            endpoint.metadata["auth_types"] = auth_types

        for evidence in analysis.auth_evidence:
            canonical = canonicalize_text(
                f"{framework}:auth:{evidence.auth_type}:{endpoint.file_path}:{evidence.line}"
            )

            auth = auth_nodes.setdefault(
                canonical,
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    canonical_name=canonical,
                    display_name=(_AUTH_DISPLAY_NAMES[evidence.auth_type]),
                    adapter_name=endpoint.adapter_name,
                    priority=endpoint.priority,
                    confidence=0.9,
                    metadata={
                        "auth_type": (evidence.auth_type),
                        "auth_detail": {
                            "protocols": [evidence.auth_type],
                            "enforcement_strict": (True),
                        },
                        "framework": framework,
                        "language": "golang",
                    },
                    file_path=endpoint.file_path,
                    line=evidence.line,
                    snippet=evidence.snippet,
                    evidence_kind="ast_call",
                ),
            )

            relationship = RelationshipHint(
                source_canonical=canonical,
                source_type=ComponentType.AUTH,
                target_canonical=(endpoint.canonical_name),
                target_type=(ComponentType.API_ENDPOINT),
                relationship_type="PROTECTS",
            )

            if relationship not in auth.relationships:
                auth.relationships.append(relationship)

    return [
        *detections,
        *auth_nodes.values(),
    ]


def analyze_http_route(
    content: str,
    result: GoParseResult,
    call: GoFunctionCall,
    *,
    framework: str,
) -> GoHTTPRouteAnalysis:
    """Recover same-file request schema and recognized auth metadata."""
    arguments = _call_arguments(call.source_snippet or "")
    handler = _handler_expression(
        arguments,
        framework,
    )
    body = _handler_body(
        content,
        handler,
    )

    schema: dict[str, str] = {}

    if body:
        binding_style = "net_http" if framework == "chi" else framework
        variable = _bound_request_variable(
            body,
            binding_style,
        )
        type_name = _variable_type(body, variable) if variable else ""

        if type_name:
            schema = _struct_schemas(content).get(type_name, {})

    chat_key, chat_list = _chat_payload(schema)
    auth_evidence = _auth_evidence_for_route(
        content,
        result,
        call,
        arguments,
        framework=framework,
    )

    return GoHTTPRouteAnalysis(
        request_body_schema=schema,
        chat_payload_key=chat_key,
        chat_payload_list=chat_list,
        auth_evidence=auth_evidence,
    )


def _resolved_string_argument(
    call: GoFunctionCall,
    index: int,
) -> str:
    if index < 0 or index >= len(call.positional_args):
        return ""

    value = call.positional_args[index]

    if not isinstance(value, str):
        return ""

    value = value.strip()

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        value = value[1:-1].strip()

    if not value or value.startswith("$"):
        return ""

    return value


def _handler_expression(
    arguments: list[str],
    framework: str,
) -> str:
    if len(arguments) < 2:
        return ""

    expression = arguments[-1] if framework == "gin" else arguments[1]
    expression = expression.strip()
    wrapper = _HANDLER_WRAPPER_RE.fullmatch(expression)

    return wrapper.group("handler") if wrapper else expression


def _handler_body(
    content: str,
    expression: str,
) -> str:
    expression = expression.strip()

    if not expression:
        return ""

    if expression.startswith("func"):
        masked = _mask_non_code(expression)
        open_brace = masked.find("{")

        if open_brace < 0:
            return ""

        close_brace = _find_balanced_end(
            masked,
            open_brace,
            "{",
            "}",
        )

        if close_brace is None:
            return ""

        return expression[open_brace + 1 : close_brace]

    if not _IDENTIFIER_RE.fullmatch(expression):
        return ""

    return _function_bodies(content).get(expression, "")


def _function_bodies(
    content: str,
) -> dict[str, str]:
    masked = _mask_non_code(content)
    bodies: dict[str, str] = {}

    for match in _FUNCTION_RE.finditer(masked):
        open_brace = masked.find(
            "{",
            match.end(),
        )

        if open_brace < 0:
            continue

        close_brace = _find_balanced_end(
            masked,
            open_brace,
            "{",
            "}",
        )

        if close_brace is None:
            continue

        bodies.setdefault(
            match.group("name"),
            content[open_brace + 1 : close_brace],
        )

    return bodies


def _bound_request_variable(
    body: str,
    framework: str,
) -> str:
    pattern = _BIND_PATTERNS.get(framework)

    if pattern is None:
        return ""

    match = pattern.search(_mask_non_code(body))

    return match.group("variable") if match else ""


def _variable_type(
    body: str,
    variable: str,
) -> str:
    escaped = re.escape(variable)
    masked = _mask_non_code(body)

    patterns = (
        re.compile(
            rf"\bvar\s+{escaped}\s+\*?"
            rf"(?P<type>"
            rf"[A-Za-z_][A-Za-z0-9_]*"
            rf")\b"
        ),
        re.compile(
            rf"\b{escaped}\s*"
            rf"(?::=|=)\s*&?\s*"
            rf"(?P<type>"
            rf"[A-Za-z_][A-Za-z0-9_]*"
            rf")\s*\{{"
        ),
        re.compile(
            rf"\b{escaped}\s*"
            rf"(?::=|=)\s*new\s*\(\s*"
            rf"(?P<type>"
            rf"[A-Za-z_][A-Za-z0-9_]*"
            rf")\s*\)"
        ),
    )

    for pattern in patterns:
        match = pattern.search(masked)

        if match:
            return match.group("type")

    return ""


def _struct_schemas(
    content: str,
) -> dict[str, dict[str, str]]:
    masked = _mask_non_code(content)
    schemas: dict[
        str,
        dict[str, str],
    ] = {}

    for match in _TYPE_STRUCT_RE.finditer(masked):
        open_brace = match.end() - 1
        close_brace = _find_balanced_end(
            masked,
            open_brace,
            "{",
            "}",
        )

        if close_brace is None:
            continue

        schema: dict[str, str] = {}
        body = content[open_brace + 1 : close_brace]

        for raw_line in body.splitlines():
            line = _strip_line_comment(raw_line).strip().rstrip(";")

            if not line or "struct {" in line:
                continue

            tag_match = re.search(
                r"`(?P<tag>[^`]*)`\s*$",
                line,
            )
            tag = tag_match.group("tag") if tag_match else ""
            declaration = line[: tag_match.start()] if tag_match else line

            field_match = _FIELD_RE.fullmatch(declaration.strip())

            if field_match is None:
                continue

            type_name = field_match.group("type").strip()

            if "{" in type_name or "}" in type_name:
                continue

            json_name: str | None = None
            json_tag = _JSON_TAG_RE.search(tag)

            if json_tag:
                json_name = json_tag.group("value").split(",", 1)[0]

                if json_name == "-":
                    continue

            for field_name in field_match.group("names").split(","):
                field_name = field_name.strip()

                if not field_name or not field_name[0].isupper():
                    continue

                schema[json_name or field_name] = type_name.lstrip("*")

        if schema:
            schemas[match.group("name")] = schema

    return schemas


def _chat_payload(
    schema: dict[str, str],
) -> tuple[str | None, bool]:
    normalized = {
        _normalize_field_name(field): (
            field,
            type_name,
        )
        for field, type_name in schema.items()
    }

    for candidate in _PROMPT_FIELD_NAMES:
        if candidate in normalized:
            field, type_name = normalized[candidate]
            return (
                field,
                _is_list_type(type_name),
            )

    for candidate in _MESSAGE_HISTORY_FIELD_NAMES:
        if candidate not in normalized:
            continue

        field, type_name = normalized[candidate]

        if _is_message_list_type(type_name):
            return field, True

    for field, type_name in schema.items():
        if type_name.strip().lstrip("*") == "string":
            return field, False

    return None, False


def _normalize_field_name(
    value: str,
) -> str:
    value = value.replace("-", "_")
    value = re.sub(
        r"(?<!^)(?=[A-Z])",
        "_",
        value,
    )
    return value.casefold()


def _is_list_type(
    value: str,
) -> bool:
    return value.strip().lstrip("*").startswith("[")


def _is_message_list_type(
    value: str,
) -> bool:
    normalized = value.replace(" ", "").lstrip("*")

    return normalized.startswith("[]") and normalized != "[]string"


def _auth_evidence_for_route(
    content: str,
    result: GoParseResult,
    route_call: GoFunctionCall,
    route_arguments: list[str],
    *,
    framework: str,
) -> tuple[GoHTTPAuthEvidence, ...]:
    if framework not in {
        "gin",
        "echo",
    }:
        return ()

    aliases = _auth_aliases(result)
    jwt_variables = _jwt_middleware_variables(
        content,
        result,
        aliases["gin_jwt"],
    )

    candidates = [
        (
            expression,
            route_call.line,
        )
        for expression in (
            _route_middleware_expressions(
                route_arguments,
                framework,
            )
        )
    ]

    protected_receivers: set[str] = set()

    if route_call.receiver:
        protected_receivers.add(route_call.receiver)

    processed_groups: set[tuple[int, str]] = set()

    changed = True

    while changed:
        changed = False

        for parsed_call in result.function_calls:
            if (
                parsed_call.line > route_call.line
                or parsed_call.function_name != "Group"
                or not parsed_call.assigned_to
                or parsed_call.assigned_to not in protected_receivers
            ):
                continue

            group_key = (
                parsed_call.line,
                parsed_call.assigned_to,
            )

            if group_key not in processed_groups:
                processed_groups.add(group_key)

                group_arguments = _call_arguments(parsed_call.source_snippet or "")

                candidates.extend(
                    (
                        expression,
                        parsed_call.line,
                    )
                    for expression in (group_arguments[1:])
                )

            if parsed_call.receiver and parsed_call.receiver not in protected_receivers:
                protected_receivers.add(parsed_call.receiver)
                changed = True

    for parsed_call in result.function_calls:
        if (
            parsed_call.line > route_call.line
            or parsed_call.function_name != "Use"
            or parsed_call.receiver not in protected_receivers
        ):
            continue

        candidates.extend(
            (
                expression,
                parsed_call.line,
            )
            for expression in _call_arguments(parsed_call.source_snippet or "")
        )

    evidence: dict[
        str,
        GoHTTPAuthEvidence,
    ] = {}

    for expression, line in candidates:
        match = _auth_match(
            expression,
            aliases,
            jwt_variables,
        )

        if match is None:
            continue

        auth_type, snippet = match

        evidence.setdefault(
            auth_type,
            GoHTTPAuthEvidence(
                auth_type=auth_type,
                line=line,
                snippet=snippet,
            ),
        )

    return tuple(evidence.values())


def _route_middleware_expressions(
    arguments: list[str],
    framework: str,
) -> list[str]:
    if framework == "gin":
        return arguments[1:-1] if len(arguments) > 2 else []

    if framework == "echo":
        return arguments[2:] if len(arguments) > 2 else []

    return []


def _auth_aliases(
    result: GoParseResult,
) -> dict[str, set[str]]:
    aliases: dict[
        str,
        set[str],
    ] = {
        "gin": set(),
        "echo_jwt": set(),
        "echo_middleware": set(),
        "gin_jwt": set(),
    }

    for imported in result.imports:
        key: str | None = None
        default_alias = ""

        if imported.path == _GIN_MODULE:
            key, default_alias = (
                "gin",
                "gin",
            )

        elif imported.path in (_ECHO_JWT_MODULES):
            key, default_alias = (
                "echo_jwt",
                "echojwt",
            )

        elif imported.path in (_ECHO_MIDDLEWARE_MODULES):
            key, default_alias = (
                "echo_middleware",
                "middleware",
            )

        elif imported.path in (_GIN_JWT_MODULES):
            key, default_alias = (
                "gin_jwt",
                "jwt",
            )

        if key is None or imported.alias in {
            "_",
            ".",
        }:
            continue

        aliases[key].add(imported.alias or default_alias)

    return aliases


def _jwt_middleware_variables(
    content: str,
    result: GoParseResult,
    aliases: set[str],
) -> set[str]:
    variables: set[str] = set()

    for call in result.function_calls:
        if call.receiver in aliases and call.function_name == "New" and call.assigned_to:
            variables.add(call.assigned_to)

    masked = _mask_non_code(content)

    for alias in aliases:
        pattern = re.compile(
            rf"(?m)^\s*"
            rf"(?P<variable>"
            rf"[A-Za-z_][A-Za-z0-9_]*"
            rf")"
            rf"(?:\s*,\s*"
            rf"[A-Za-z_][A-Za-z0-9_]*"
            rf")*\s*:=\s*"
            rf"{re.escape(alias)}"
            rf"\.New\s*\("
        )

        variables.update(match.group("variable") for match in pattern.finditer(masked))

    return variables


def _auth_match(
    expression: str,
    aliases: dict[str, set[str]],
    jwt_variables: set[str],
) -> tuple[str, str] | None:
    compact = re.sub(
        r"\s+",
        "",
        expression,
    )

    for alias in aliases["gin"]:
        for name in (
            "BasicAuth",
            "BasicAuthForRealm",
        ):
            if compact.startswith(f"{alias}.{name}("):
                return (
                    "basic",
                    f"{alias}.{name}(...)",
                )

    for alias in aliases["echo_jwt"]:
        for name in (
            "JWT",
            "JWTWithConfig",
            "WithConfig",
        ):
            if compact.startswith(f"{alias}.{name}("):
                return (
                    "jwt",
                    f"{alias}.{name}(...)",
                )

    for alias in aliases["echo_middleware"]:
        for name in (
            "JWT",
            "JWTWithConfig",
        ):
            if compact.startswith(f"{alias}.{name}("):
                return (
                    "jwt",
                    f"{alias}.{name}(...)",
                )

        for name in (
            "BasicAuth",
            "BasicAuthWithConfig",
        ):
            if compact.startswith(f"{alias}.{name}("):
                return (
                    "basic",
                    f"{alias}.{name}(...)",
                )

    for variable in jwt_variables:
        if compact.startswith(f"{variable}.MiddlewareFunc("):
            return (
                "jwt",
                f"{variable}.MiddlewareFunc(...)",
            )

    return None


def _call_arguments(
    source_snippet: str,
) -> list[str]:
    open_paren = source_snippet.find("(")

    if open_paren < 0:
        return []

    close_paren = _find_balanced_end(
        source_snippet,
        open_paren,
        "(",
        ")",
    )

    if close_paren is None:
        return []

    return _split_top_level(source_snippet[open_paren + 1 : close_paren])


def _split_top_level(
    value: str,
) -> list[str]:
    arguments: list[str] = []
    start = 0
    depths = {
        "(": 0,
        "[": 0,
        "{": 0,
    }
    closers = {
        ")": "(",
        "]": "[",
        "}": "{",
    }
    quote: str | None = None
    escaped = False

    for index, char in enumerate(value):
        if quote is not None:
            if quote != "`" and escaped:
                escaped = False

            elif quote != "`" and char == "\\":
                escaped = True

            elif char == quote:
                quote = None

            continue

        if char in {
            '"',
            "'",
            "`",
        }:
            quote = char

        elif char in depths:
            depths[char] += 1

        elif char in closers:
            depths[closers[char]] = max(
                0,
                depths[closers[char]] - 1,
            )

        elif char == "," and not any(depths.values()):
            arguments.append(value[start:index].strip())
            start = index + 1

    tail = value[start:].strip()

    if tail:
        arguments.append(tail)

    return arguments


def _mask_non_code(
    value: str,
) -> str:
    masked = list(value)
    index = 0

    def blank(
        start: int,
        end: int,
    ) -> None:
        for position in range(start, end):
            if masked[position] != "\n":
                masked[position] = " "

    while index < len(value):
        if value.startswith("//", index):
            end = value.find("\n", index)
            end = len(value) if end == -1 else end
            blank(index, end)
            index = end
            continue

        if value.startswith("/*", index):
            end = value.find(
                "*/",
                index + 2,
            )
            end = len(value) if end == -1 else end + 2
            blank(index, end)
            index = end
            continue

        if value[index] in {
            '"',
            "'",
            "`",
        }:
            quote = value[index]
            start = index
            index += 1

            while index < len(value):
                if quote != "`" and value[index] == "\\":
                    index += 2
                    continue

                if value[index] == quote:
                    index += 1
                    break

                index += 1

            blank(
                start,
                min(index, len(value)),
            )
            continue

        index += 1

    return "".join(masked)


def _find_balanced_end(
    text: str,
    start: int,
    opener: str,
    closer: str,
) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if quote is not None:
            if quote != "`" and escaped:
                escaped = False

            elif quote != "`" and char == "\\":
                escaped = True

            elif char == quote:
                quote = None

            continue

        if char in {
            '"',
            "'",
            "`",
        }:
            quote = char

        elif char == opener:
            depth += 1

        elif char == closer:
            depth -= 1

            if depth == 0:
                return index

    return None


def _strip_line_comment(
    value: str,
) -> str:
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(value):
        char = value[index]

        if quote is not None:
            if quote != "`" and escaped:
                escaped = False

            elif quote != "`" and char == "\\":
                escaped = True

            elif char == quote:
                quote = None

            index += 1
            continue

        if char in {
            '"',
            "'",
            "`",
        }:
            quote = char
            index += 1
            continue

        if value.startswith("//", index):
            return value[:index]

        index += 1

    return value


__all__ = [
    "GoHTTPAuthEvidence",
    "GoHTTPRouteAnalysis",
    "analyze_http_route",
    "enrich_http_endpoint_detections",
]
