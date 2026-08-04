"""Structured Go source parsing for SBOM and AI-framework extraction.

The parser prefers ``tree_sitter_go`` for accurate syntax-tree extraction and
falls back to a best-effort regex parser when the optional grammar cannot be
loaded. It extracts imports, struct/constructor instantiations, calls, and
string literals while preserving partial results for malformed source.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

try:
    import tree_sitter
    import tree_sitter_go

    HAS_TREE_SITTER = True
except ImportError:
    tree_sitter = None  # type: ignore[assignment]
    tree_sitter_go = None  # type: ignore[assignment]
    HAS_TREE_SITTER = False


@dataclass
class GoImport:
    """Information about one Go import specification."""

    path: str
    alias: str | None
    line: int

    @property
    def module(self) -> str:
        """Return the import path using the shared parser terminology."""

        return self.path


@dataclass
class GoInstantiation:
    """A named struct literal or conventional ``New*`` constructor call."""

    class_name: str
    args: dict[str, Any] = field(default_factory=dict)
    positional_args: list[Any] = field(default_factory=list)
    assigned_to: str | None = None
    line: int = 0
    line_end: int = 0
    kind: str = "struct_literal"
    source_snippet: str | None = None


@dataclass
class GoFunctionCall:
    """Information about a Go function or method call."""

    function_name: str
    receiver: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    positional_args: list[Any] = field(default_factory=list)
    assigned_to: str | None = None
    line: int = 0
    line_end: int = 0
    source_snippet: str | None = None

    @property
    def full_name(self) -> str:
        """Return the receiver-qualified function name."""

        if self.receiver:
            return f"{self.receiver}.{self.function_name}"
        return self.function_name


@dataclass
class GoStringLiteral:
    """A raw or interpreted Go string literal."""

    value: str
    line: int
    context: str | None = None
    assigned_to: str | None = None
    is_raw: bool = False


@dataclass
class GoParseResult:
    """Structured information extracted from one Go source file."""

    imports: list[GoImport] = field(default_factory=list)
    instantiations: list[GoInstantiation] = field(default_factory=list)
    function_calls: list[GoFunctionCall] = field(default_factory=list)
    string_literals: list[GoStringLiteral] = field(default_factory=list)
    source: str = ""
    file_path: str = ""
    parse_error: str | None = None
    used_tree_sitter: bool = False

    def __bool__(self) -> bool:
        return bool(
            self.imports or self.instantiations or self.function_calls or self.string_literals
        )


_GO_LANGUAGE: Any | None = None
_NAMED_TYPE_NODES = {"type_identifier", "qualified_type", "generic_type"}
_STRING_NODES = {"interpreted_string_literal", "raw_string_literal"}
_ASSIGNMENT_NODES = {
    "assignment_statement",
    "short_var_declaration",
    "const_spec",
    "var_spec",
}
_WRAPPER_NODES = {
    "expression_list",
    "parenthesized_expression",
    "unary_expression",
}


def get_go_parser() -> Any | None:
    """Return a tree-sitter Go parser, or ``None`` when unavailable."""

    global _GO_LANGUAGE

    if not HAS_TREE_SITTER:
        return None

    assert tree_sitter is not None
    assert tree_sitter_go is not None

    if _GO_LANGUAGE is None:
        _GO_LANGUAGE = tree_sitter.Language(tree_sitter_go.language())

    return tree_sitter.Parser(_GO_LANGUAGE)


def parse_go(content: str, file_path: str = "") -> GoParseResult:
    """Parse Go source into a structured :class:`GoParseResult`.

    Tree-sitter is attempted first. If the bindings are missing or parser
    initialization fails, a best-effort regex parser is used instead.
    """

    parser_error: str | None = None

    try:
        parser = get_go_parser()
    except Exception as exc:  # pragma: no cover - platform-specific binding failures
        parser = None
        parser_error = f"tree-sitter initialization failed: {exc}"

    if parser is not None:
        try:
            return _parse_with_tree_sitter(content, file_path, parser)
        except Exception as exc:  # pragma: no cover - defensive fallback
            parser_error = f"tree-sitter parsing failed: {exc}"

    result = _parse_with_regex(content, file_path)

    if parser_error:
        result.parse_error = parser_error

    return result


def _parse_with_tree_sitter(
    content: str,
    file_path: str,
    parser: Any,
) -> GoParseResult:
    source = content.encode("utf-8")
    tree = parser.parse(source)
    root = tree.root_node
    symbols = _build_symbol_table(root, source)

    result = GoParseResult(
        source=content,
        file_path=file_path,
        used_tree_sitter=True,
    )

    for node in _walk(root):
        if node.type == "import_spec":
            imported = _tree_sitter_import(node, source)
            if imported is not None:
                result.imports.append(imported)

        elif node.type == "composite_literal":
            instantiation = _tree_sitter_struct_literal(
                node,
                source,
                symbols,
            )
            if instantiation is not None:
                result.instantiations.append(instantiation)

        elif node.type == "call_expression":
            call = _tree_sitter_call(node, source, symbols)

            if call is not None:
                result.function_calls.append(call)

                if call.function_name.startswith("New"):
                    result.instantiations.append(
                        GoInstantiation(
                            class_name=call.full_name,
                            args=dict(call.args),
                            positional_args=list(call.positional_args),
                            assigned_to=call.assigned_to,
                            line=call.line,
                            line_end=call.line_end,
                            kind="constructor_call",
                            source_snippet=call.source_snippet,
                        )
                    )

        elif node.type in _STRING_NODES and not _is_non_value_string(node):
            result.string_literals.append(_tree_sitter_string(node, source))

    if root.has_error:
        error_line = _first_error_line(root)
        result.parse_error = f"Go syntax error near line {error_line}"

    return result


def _walk(node: Any) -> Iterator[Any]:
    yield node

    for child in node.children:
        yield from _walk(child)


def _node_text(node: Any | None, source: bytes) -> str:
    if node is None:
        return ""

    return source[node.start_byte : node.end_byte].decode(
        "utf-8",
        errors="replace",
    )


def _line(node: Any) -> int:
    return int(node.start_point[0]) + 1


def _line_end(node: Any) -> int:
    return int(node.end_point[0]) + 1


def _named_children(node: Any | None) -> list[Any]:
    if node is None:
        return []

    return list(node.named_children)


def _children_by_field_name(
    node: Any,
    field_name: str,
) -> list[Any]:
    method = getattr(node, "children_by_field_name", None)

    if callable(method):
        return list(method(field_name))

    child = node.child_by_field_name(field_name)
    return [child] if child is not None else []


def _tree_sitter_import(
    node: Any,
    source: bytes,
) -> GoImport | None:
    path_node = node.child_by_field_name("path")

    if path_node is None:
        return None

    alias_node = node.child_by_field_name("name")

    path = _decode_go_string(
        _node_text(path_node, source),
        is_raw=path_node.type == "raw_string_literal",
    )

    alias = _node_text(alias_node, source) or None

    return GoImport(
        path=path,
        alias=alias,
        line=_line(node),
    )


def _tree_sitter_struct_literal(
    node: Any,
    source: bytes,
    symbols: dict[str, Any],
) -> GoInstantiation | None:
    type_node = node.child_by_field_name("type")
    body_node = node.child_by_field_name("body")

    if type_node is None or body_node is None or type_node.type not in _NAMED_TYPE_NODES:
        return None

    type_name = _node_text(type_node, source)

    args, positional_args = _literal_body_values(
        body_node,
        source,
        symbols,
    )

    return GoInstantiation(
        class_name=type_name,
        args=args,
        positional_args=positional_args,
        assigned_to=_assignment_target(node, source),
        line=_line(node),
        line_end=_line_end(node),
        kind="struct_literal",
        source_snippet=_node_text(node, source),
    )


def _tree_sitter_call(
    node: Any,
    source: bytes,
    symbols: dict[str, Any],
) -> GoFunctionCall | None:
    function_node = node.child_by_field_name("function")
    arguments_node = node.child_by_field_name("arguments")

    if function_node is None or arguments_node is None:
        return None

    callee = _node_text(function_node, source)
    receiver, function_name = _split_callee(callee)

    positional_args = [
        _extract_value(child, source, symbols) for child in _named_children(arguments_node)
    ]

    return GoFunctionCall(
        function_name=function_name,
        receiver=receiver,
        positional_args=positional_args,
        assigned_to=_assignment_target(node, source),
        line=_line(node),
        line_end=_line_end(node),
        source_snippet=_node_text(node, source),
    )


def _tree_sitter_string(
    node: Any,
    source: bytes,
) -> GoStringLiteral:
    raw_text = _node_text(node, source)

    return GoStringLiteral(
        value=_decode_go_string(
            raw_text,
            is_raw=node.type == "raw_string_literal",
        ),
        line=_line(node),
        context=_enclosing_context(node, source),
        assigned_to=_assignment_target(node, source),
        is_raw=node.type == "raw_string_literal",
    )


def _split_callee(callee: str) -> tuple[str | None, str]:
    normalized = re.sub(
        r"\[[^\]]*\]$",
        "",
        callee.strip(),
    )

    if "." not in normalized:
        return None, normalized

    receiver, function_name = normalized.rsplit(".", 1)
    return receiver, function_name


def _literal_body_values(
    body_node: Any,
    source: bytes,
    symbols: dict[str, Any],
) -> tuple[dict[str, Any], list[Any]]:
    args: dict[str, Any] = {}
    positional_args: list[Any] = []

    for child in _named_children(body_node):
        child = _unwrap_literal_element(child)

        if child.type == "keyed_element":
            key_node = child.child_by_field_name("key")
            value_node = child.child_by_field_name("value")

            if key_node is not None and value_node is not None:
                args[_node_text(key_node, source)] = _extract_value(
                    value_node,
                    source,
                    symbols,
                )
        else:
            positional_args.append(_extract_value(child, source, symbols))

    return args, positional_args


def _unwrap_literal_element(node: Any) -> Any:
    if node.type == "literal_element" and len(node.named_children) == 1:
        return node.named_children[0]

    return node


def _extract_value(
    node: Any,
    source: bytes,
    symbols: dict[str, Any],
) -> Any:
    node = _unwrap_literal_element(node)
    text = _node_text(node, source).strip()

    if node.type == "interpreted_string_literal":
        return _decode_go_string(text, is_raw=False)

    if node.type == "raw_string_literal":
        return _decode_go_string(text, is_raw=True)

    if node.type == "int_literal":
        return _parse_int(text)

    if node.type == "float_literal":
        try:
            return float(text.replace("_", ""))
        except ValueError:
            return text

    if node.type == "true":
        return True

    if node.type == "false":
        return False

    if node.type == "nil":
        return None

    if node.type == "identifier":
        return symbols.get(text, f"${text}")

    if node.type == "parenthesized_expression" and node.named_children:
        return _extract_value(
            node.named_children[0],
            source,
            symbols,
        )

    if node.type == "unary_expression" and node.named_children:
        value = _extract_value(
            node.named_children[-1],
            source,
            symbols,
        )

        if text.startswith("-") and isinstance(value, int | float):
            return -value

        return value

    if node.type == "composite_literal":
        type_node = node.child_by_field_name("type")
        body_node = node.child_by_field_name("body")

        if body_node is not None:
            args, positional_args = _literal_body_values(
                body_node,
                source,
                symbols,
            )

            structured: dict[str, Any] = {}

            if type_node is not None:
                structured["$type"] = _node_text(
                    type_node,
                    source,
                )

            if args:
                structured.update(args)

            if positional_args:
                structured["$items"] = positional_args

            return structured

    if node.type == "literal_value":
        args, positional_args = _literal_body_values(
            node,
            source,
            symbols,
        )

        if args and not positional_args:
            return args

        if not args:
            return positional_args

        return {
            **args,
            "$items": positional_args,
        }

    return f"${text}" if text else None


def _parse_int(text: str) -> int | str:
    normalized = text.replace("_", "")

    try:
        if re.fullmatch(r"0[0-7]+", normalized):
            return int(normalized, 8)

        return int(normalized, 0)
    except ValueError:
        return text


def _decode_go_string(
    text: str,
    *,
    is_raw: bool,
) -> str:
    if len(text) < 2:
        return text

    if is_raw:
        return text[1:-1].replace("\r", "")

    try:
        value = ast.literal_eval(text)

        if isinstance(value, str):
            return value

        return text[1:-1]
    except (SyntaxError, ValueError):
        return text[1:-1]


def _build_symbol_table(
    root: Any,
    source: bytes,
) -> dict[str, Any]:
    symbols: dict[str, Any] = {}

    for node in _walk(root):
        if node.type not in _ASSIGNMENT_NODES:
            continue

        if node.type in {"const_spec", "var_spec"}:
            names = [
                child
                for child in _children_by_field_name(node, "name")
                if child.type == "identifier"
            ]
            value_container = node.child_by_field_name("value")
        else:
            left = node.child_by_field_name("left")
            names = [child for child in _named_children(left) if child.type == "identifier"]
            value_container = node.child_by_field_name("right")

        values = _named_children(value_container)

        for name_node, value_node in zip(names, values):
            name = _node_text(name_node, source)
            symbols[name] = _extract_value(
                value_node,
                source,
                symbols,
            )

    return symbols


def _assignment_target(
    node: Any,
    source: bytes,
) -> str | None:
    current = node
    parent = current.parent

    while parent is not None and parent.type in _WRAPPER_NODES:
        current = parent
        parent = parent.parent

    if parent is None or parent.type not in _ASSIGNMENT_NODES:
        return None

    if parent.type in {"const_spec", "var_spec"}:
        name_nodes = _children_by_field_name(parent, "name")
    else:
        left = parent.child_by_field_name("left")
        name_nodes = _named_children(left)

    if not name_nodes:
        return None

    return _node_text(name_nodes[0], source) or None


def _enclosing_context(
    node: Any,
    source: bytes,
) -> str | None:
    parent = node.parent

    while parent is not None:
        if parent.type in {
            "function_declaration",
            "method_declaration",
        }:
            name_node = parent.child_by_field_name("name")
            return _node_text(name_node, source) or None

        parent = parent.parent

    return None


def _is_non_value_string(node: Any) -> bool:
    parent = node.parent

    while parent is not None:
        if parent.type in {
            "import_spec",
            "field_declaration",
        }:
            return True

        if parent.type in {
            "source_file",
            "function_declaration",
            "method_declaration",
        }:
            return False

        parent = parent.parent

    return False


def _first_error_line(root: Any) -> int:
    for node in _walk(root):
        if node.type == "ERROR" or node.is_missing:
            return _line(node)

    return 1


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------

_IMPORT_SINGLE_RE = re.compile(
    r"(?m)^\s*import\s+"
    r"(?:(?P<alias>[A-Za-z_][A-Za-z0-9_]*|[._])\s+)?"
    r'(?P<path>"(?:\\.|[^"\\])*")\s*(?://.*)?$'
)

_IMPORT_BLOCK_RE = re.compile(r"(?ms)^\s*import\s*\((?P<body>.*?)^\s*\)")

_IMPORT_SPEC_RE = re.compile(
    r"(?m)^\s*"
    r"(?:(?P<alias>[A-Za-z_][A-Za-z0-9_]*|[._])\s+)?"
    r'(?P<path>"(?:\\.|[^"\\])*")\s*(?://.*)?$'
)

_CALL_RE = re.compile(
    r"\b"
    r"(?P<callee>"
    r"[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r")\s*\("
)

_STRUCT_RE = re.compile(
    r"\b"
    r"(?P<type>"
    r"[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r")\s*\{"
)

_SYMBOL_RE = re.compile(
    r"(?m)^\s*"
    r"(?:const|var)?\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+[A-Za-z_][A-Za-z0-9_.\[\]*]*)?"
    r"\s*(?::=|=)\s*"
    r"(?P<value>"
    r"`[^`]*`|"
    r'"(?:\\.|[^"\\])*"|'
    r"true|false|nil|[-+]?\d[\d_]*"
    r")\s*$"
)

_SKIP_CALL_NAMES = {
    "append",
    "cap",
    "clear",
    "close",
    "complex",
    "copy",
    "defer",
    "delete",
    "for",
    "func",
    "go",
    "if",
    "imag",
    "import",
    "len",
    "make",
    "max",
    "min",
    "new",
    "panic",
    "print",
    "println",
    "real",
    "recover",
    "return",
    "select",
    "switch",
}


@dataclass
class _ScannedString:
    value: str
    start: int
    end: int
    line: int
    is_raw: bool


def _parse_with_regex(
    content: str,
    file_path: str,
) -> GoParseResult:
    result = GoParseResult(
        source=content,
        file_path=file_path,
        used_tree_sitter=False,
    )

    result.imports = _regex_imports(content)
    symbols = _regex_symbols(content)
    masked, strings = _mask_non_code(content)

    result.instantiations = _regex_struct_literals(
        content,
        masked,
        symbols,
    )

    calls, constructors = _regex_calls(
        content,
        masked,
        symbols,
    )

    result.function_calls = calls
    result.instantiations.extend(constructors)

    import_keys = {(item.line, item.path) for item in result.imports}

    result.string_literals = [
        GoStringLiteral(
            value=item.value,
            line=item.line,
            assigned_to=_regex_assignment_target(
                content,
                item.start,
            ),
            is_raw=item.is_raw,
        )
        for item in strings
        if (item.line, item.value) not in import_keys
    ]

    return result


def _regex_imports(content: str) -> list[GoImport]:
    imports: list[GoImport] = []

    for match in _IMPORT_SINGLE_RE.finditer(content):
        imports.append(
            GoImport(
                path=_decode_go_string(
                    match.group("path"),
                    is_raw=False,
                ),
                alias=match.group("alias"),
                line=content.count(
                    "\n",
                    0,
                    match.start(),
                )
                + 1,
            )
        )

    for block in _IMPORT_BLOCK_RE.finditer(content):
        body = block.group("body")
        body_start = block.start("body")

        for match in _IMPORT_SPEC_RE.finditer(body):
            imports.append(
                GoImport(
                    path=_decode_go_string(
                        match.group("path"),
                        is_raw=False,
                    ),
                    alias=match.group("alias"),
                    line=content.count(
                        "\n",
                        0,
                        body_start + match.start("path"),
                    )
                    + 1,
                )
            )

    return sorted(
        imports,
        key=lambda item: item.line,
    )


def _regex_symbols(content: str) -> dict[str, Any]:
    symbols: dict[str, Any] = {}

    for match in _SYMBOL_RE.finditer(content):
        symbols[match.group("name")] = _parse_value_text(
            match.group("value"),
            symbols,
        )

    return symbols


def _regex_struct_literals(
    content: str,
    masked: str,
    symbols: dict[str, Any],
) -> list[GoInstantiation]:
    instantiations: list[GoInstantiation] = []
    seen: set[tuple[int, int]] = set()

    for match in _STRUCT_RE.finditer(masked):
        type_name = match.group("type")

        if not type_name.rsplit(".", 1)[-1][:1].isupper():
            continue

        open_brace = match.end() - 1
        close_brace = _find_matching(
            masked,
            open_brace,
            "{",
            "}",
        )

        if close_brace is None or (match.start(), close_brace) in seen:
            continue

        seen.add((match.start(), close_brace))

        args, positional_args = _parse_regex_literal_body(
            content[open_brace + 1 : close_brace],
            symbols,
        )

        instantiations.append(
            GoInstantiation(
                class_name=type_name,
                args=args,
                positional_args=positional_args,
                assigned_to=_regex_assignment_target(
                    content,
                    match.start(),
                ),
                line=content.count(
                    "\n",
                    0,
                    match.start(),
                )
                + 1,
                line_end=content.count(
                    "\n",
                    0,
                    close_brace,
                )
                + 1,
                kind="struct_literal",
                source_snippet=content[match.start() : close_brace + 1],
            )
        )

    return instantiations


def _regex_calls(
    content: str,
    masked: str,
    symbols: dict[str, Any],
) -> tuple[
    list[GoFunctionCall],
    list[GoInstantiation],
]:
    calls: list[GoFunctionCall] = []
    constructors: list[GoInstantiation] = []
    seen: set[tuple[int, int]] = set()

    for match in _CALL_RE.finditer(masked):
        if _looks_like_function_declaration(
            masked,
            match.start(),
        ):
            continue

        callee = match.group("callee")
        receiver, function_name = _split_callee(callee)

        if receiver is None and function_name in _SKIP_CALL_NAMES:
            continue

        open_paren = match.end() - 1
        close_paren = _find_matching(
            masked,
            open_paren,
            "(",
            ")",
        )

        if close_paren is None or (match.start(), close_paren) in seen:
            continue

        seen.add((match.start(), close_paren))

        positional_args = [
            _parse_value_text(argument, symbols)
            for argument in _split_top_level(content[open_paren + 1 : close_paren])
            if argument.strip()
        ]

        call = GoFunctionCall(
            function_name=function_name,
            receiver=receiver,
            positional_args=positional_args,
            assigned_to=_regex_assignment_target(
                content,
                match.start(),
            ),
            line=content.count(
                "\n",
                0,
                match.start(),
            )
            + 1,
            line_end=content.count(
                "\n",
                0,
                close_paren,
            )
            + 1,
            source_snippet=content[match.start() : close_paren + 1],
        )

        calls.append(call)

        if function_name.startswith("New"):
            constructors.append(
                GoInstantiation(
                    class_name=call.full_name,
                    positional_args=list(call.positional_args),
                    assigned_to=call.assigned_to,
                    line=call.line,
                    line_end=call.line_end,
                    kind="constructor_call",
                    source_snippet=call.source_snippet,
                )
            )

    return calls, constructors


def _parse_regex_literal_body(
    body: str,
    symbols: dict[str, Any],
) -> tuple[dict[str, Any], list[Any]]:
    args: dict[str, Any] = {}
    positional_args: list[Any] = []

    for element in _split_top_level(body):
        if not element.strip():
            continue

        key_value = _split_top_level_key_value(element)

        if key_value is None:
            positional_args.append(_parse_value_text(element, symbols))
        else:
            key, value = key_value
            args[key.strip()] = _parse_value_text(
                value,
                symbols,
            )

    return args, positional_args


def _parse_value_text(
    text: str,
    symbols: dict[str, Any],
) -> Any:
    value = text.strip()

    while value.startswith(("&", "*")):
        value = value[1:].strip()

    if len(value) >= 2 and value[0] == value[-1] == "`":
        return _decode_go_string(value, is_raw=True)

    if len(value) >= 2 and value[0] == value[-1] == '"':
        return _decode_go_string(value, is_raw=False)

    if value == "true":
        return True

    if value == "false":
        return False

    if value == "nil":
        return None

    if re.fullmatch(r"[-+]?\d[\d_]*", value):
        sign = -1 if value.startswith("-") else 1
        unsigned = value.lstrip("+-")
        parsed = _parse_int(unsigned)

        if isinstance(parsed, int):
            return sign * parsed

        return value

    if re.fullmatch(
        r"[-+]?"
        r"(?:"
        r"\d[\d_]*\.\d[\d_]*|"
        r"\d[\d_]*[eE][-+]?\d+"
        r")",
        value,
    ):
        try:
            return float(value.replace("_", ""))
        except ValueError:
            return value

    if re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*",
        value,
    ):
        return symbols.get(value, f"${value}")

    return f"${value}" if value else None


def _mask_non_code(
    content: str,
) -> tuple[str, list[_ScannedString]]:
    masked = list(content)
    strings: list[_ScannedString] = []
    index = 0
    line = 1

    def blank(start: int, end: int) -> None:
        for position in range(start, end):
            if masked[position] != "\n":
                masked[position] = " "

    while index < len(content):
        if content.startswith("//", index):
            end = content.find("\n", index)
            end = len(content) if end == -1 else end
            blank(index, end)
            index = end
            continue

        if content.startswith("/*", index):
            end = content.find("*/", index + 2)
            end = len(content) if end == -1 else end + 2
            line += content.count("\n", index, end)
            blank(index, end)
            index = end
            continue

        if content[index] in {'"', "`", "'"}:
            quote = content[index]
            start = index
            start_line = line
            index += 1

            while index < len(content):
                if quote != "`" and content[index] == "\\":
                    index += 2
                    continue

                if content[index] == quote:
                    index += 1
                    break

                if content[index] == "\n":
                    line += 1

                index += 1

            raw = content[start:index]

            if quote != "'":
                strings.append(
                    _ScannedString(
                        value=_decode_go_string(
                            raw,
                            is_raw=quote == "`",
                        ),
                        start=start,
                        end=index,
                        line=start_line,
                        is_raw=quote == "`",
                    )
                )

            blank(start, index)
            continue

        if content[index] == "\n":
            line += 1

        index += 1

    return "".join(masked), strings


def _find_matching(
    text: str,
    start: int,
    opener: str,
    closer: str,
) -> int | None:
    depth = 0

    for index in range(start, len(text)):
        char = text[index]

        if char == opener:
            depth += 1

        elif char == closer:
            depth -= 1

            if depth == 0:
                return index

    return None


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
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

    for index, char in enumerate(text):
        if quote is not None:
            if quote != "`" and escaped:
                escaped = False

            elif quote != "`" and char == "\\":
                escaped = True

            elif char == quote:
                quote = None

            continue

        if char in {'"', "'", "`"}:
            quote = char

        elif char in depths:
            depths[char] += 1

        elif char in closers:
            depths[closers[char]] = max(
                0,
                depths[closers[char]] - 1,
            )

        elif char == "," and not any(depths.values()):
            parts.append(text[start:index].strip())
            start = index + 1

    parts.append(text[start:].strip())
    return parts


def _split_top_level_key_value(
    text: str,
) -> tuple[str, str] | None:
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

    for index, char in enumerate(text):
        if quote is not None:
            if quote != "`" and escaped:
                escaped = False

            elif quote != "`" and char == "\\":
                escaped = True

            elif char == quote:
                quote = None

            continue

        if char in {'"', "'", "`"}:
            quote = char

        elif char in depths:
            depths[char] += 1

        elif char in closers:
            depths[closers[char]] = max(
                0,
                depths[closers[char]] - 1,
            )

        elif char == ":" and not any(depths.values()):
            return text[:index], text[index + 1 :]

    return None


def _regex_assignment_target(
    content: str,
    position: int,
) -> str | None:
    boundary = max(
        content.rfind("\n", 0, position),
        content.rfind(";", 0, position),
        content.rfind("{", 0, position),
    )

    prefix = content[boundary + 1 : position]

    match = re.search(
        r"^\s*"
        r"(?:(?:var|const)\s+)?"
        r"(?P<first>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*"
        r"\s*(?::=|=)\s*$",
        prefix,
    )

    return match.group("first") if match else None


def _looks_like_function_declaration(
    masked: str,
    position: int,
) -> bool:
    line_start = masked.rfind("\n", 0, position) + 1
    prefix = masked[line_start:position]

    return bool(
        re.search(
            r"\bfunc(?:\s*\([^)]*\))?\s*$",
            prefix,
        )
    )
