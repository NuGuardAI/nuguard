"""Dependency-free structural parsing for Java source files.

The parser is deliberately best-effort.  It preserves source offsets while
masking comments and literals, then extracts the imports, type declarations,
method declarations, annotations, and string literals needed by NuGuard's Java
adapters.  It does not attempt compiler-level name or type resolution.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field


@dataclass(frozen=True)
class JavaImport:
    module: str
    is_static: bool = False
    wildcard: bool = False
    line: int = 0


@dataclass(frozen=True)
class JavaTypeDeclaration:
    name: str
    kind: str
    annotations: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    base_types: tuple[str, ...] = ()
    line: int = 0
    line_end: int = 0
    signature: str = ""
    body_start: int = 0
    body_end: int = 0


@dataclass(frozen=True)
class JavaMethodDeclaration:
    name: str
    return_type: str | None
    parameters: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    containing_type: str | None = None
    is_constructor: bool = False
    line: int = 0
    line_end: int = 0
    signature: str = ""
    body: str = ""
    body_start: int = 0
    body_end: int = 0


@dataclass(frozen=True)
class JavaStringLiteral:
    value: str
    line: int
    line_end: int
    assigned_to: str | None = None
    enclosing_method: str | None = None
    is_text_block: bool = False

    @property
    def is_potential_prompt(self) -> bool:
        lowered = self.value.lower()
        return len(self.value) >= 80 or any(
            marker in lowered
            for marker in (
                "you are",
                "system:",
                "assistant:",
                "user:",
                "instructions:",
                "respond with",
            )
        )


@dataclass
class JavaParseResult:
    imports: list[JavaImport] = field(default_factory=list)
    type_declarations: list[JavaTypeDeclaration] = field(default_factory=list)
    method_declarations: list[JavaMethodDeclaration] = field(default_factory=list)
    string_literals: list[JavaStringLiteral] = field(default_factory=list)
    source: str = ""
    file_path: str = ""
    parse_error: str | None = None

    @property
    def classes(self) -> list[JavaTypeDeclaration]:
        return self.type_declarations

    @property
    def methods(self) -> list[JavaMethodDeclaration]:
        return self.method_declarations

    def __bool__(self) -> bool:
        return bool(
            self.imports
            or self.type_declarations
            or self.method_declarations
            or self.string_literals
        )


@dataclass(frozen=True)
class _LiteralToken:
    start: int
    end: int
    value: str
    is_text_block: bool


@dataclass(frozen=True)
class _Span:
    name: str
    start: int
    body_start: int
    end: int


_IDENTIFIER = r"[A-Za-z_$][\w$]*"
_ANNOTATION = rf"@{_IDENTIFIER}(?:\s*\([^)]*\))?"
_IMPORT_RE = re.compile(
    rf"(?m)^[ \t]*import\s+(?P<static>static\s+)?"
    rf"(?P<module>{_IDENTIFIER}(?:\.{_IDENTIFIER})*)"
    rf"(?P<wildcard>\.\*)?\s*;"
)
_TYPE_RE = re.compile(
    rf"(?m)^[ \t]*"
    rf"(?P<annotations>(?:(?:{_ANNOTATION})[ \t\r\n]*)*)"
    rf"(?P<modifiers>(?:(?:public|protected|private|abstract|static|final|sealed|"
    rf"non-sealed|strictfp)\s+)*)"
    rf"(?P<kind>class|interface|enum|record|@interface)\s+"
    rf"(?P<name>{_IDENTIFIER})"
    rf"(?P<tail>[^{{;]*)\{{"
)
_METHOD_RE = re.compile(
    rf"(?m)^[ \t]*"
    rf"(?P<annotations>(?:(?:{_ANNOTATION})[ \t\r\n]*)*)"
    rf"(?P<modifiers>(?:(?:public|protected|private|abstract|static|final|"
    rf"synchronized|native|strictfp|default)\s+)*)"
    rf"(?:(?P<type_params><[^;{{}}()\n]+>)\s*)?"
    rf"(?:(?P<return>[A-Za-z_$][\w$.,<>?\[\] \t]*)\s+)?"
    rf"(?P<name>{_IDENTIFIER})\s*\((?P<params>[^;{{}}()]*)\)"
    rf"(?:\s+throws\s+[^{{;]+)?\s*(?P<terminator>\{{|;)"
)
_NON_METHOD_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "synchronized",
    "return",
    "throw",
    "new",
    "this",
    "super",
}


def parse_java(content: str, file_path: str = "") -> JavaParseResult:
    """Parse *content* into a best-effort :class:`JavaParseResult`."""
    newlines = [match.start() for match in re.finditer(r"\n", content)]
    masked, literal_tokens, lexical_error = _mask_non_code(content, newlines)
    types, type_spans = _extract_types(content, masked, newlines)
    methods, method_spans = _extract_methods(
        content, masked, newlines, type_spans, {item.name for item in types}
    )
    errors = [error for error in (lexical_error, _brace_error(masked, newlines)) if error]
    return JavaParseResult(
        imports=_extract_imports(masked, newlines),
        type_declarations=types,
        method_declarations=methods,
        string_literals=_build_literals(content, masked, literal_tokens, newlines, method_spans),
        source=content,
        file_path=file_path,
        parse_error="; ".join(errors) if errors else None,
    )


def _line(newlines: list[int], position: int) -> int:
    return bisect_right(newlines, position) + 1


def _mask_range(masked: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if masked[index] != "\n":
            masked[index] = " "


def _decode_java_string(raw: str, is_text_block: bool) -> str:
    if is_text_block:
        text = raw[3:-3]
    else:
        text = raw[1:-1]
    try:
        return bytes(text, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return text


def _mask_non_code(source: str, newlines: list[int]) -> tuple[str, list[_LiteralToken], str | None]:
    masked = list(source)
    tokens: list[_LiteralToken] = []
    errors: list[str] = []
    index = 0
    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            _mask_range(masked, index, end)
            index = end
            continue
        if source.startswith("/*", index):
            closing = source.find("*/", index + 2)
            if closing < 0:
                _mask_range(masked, index, len(source))
                errors.append(f"Unterminated block comment near line {_line(newlines, index)}")
                break
            end = closing + 2
            _mask_range(masked, index, end)
            index = end
            continue
        if source.startswith('"""', index):
            closing = source.find('"""', index + 3)
            if closing < 0:
                _mask_range(masked, index, len(source))
                errors.append(f"Unterminated text block near line {_line(newlines, index)}")
                break
            end = closing + 3
            raw = source[index:end]
            tokens.append(_LiteralToken(index, end, _decode_java_string(raw, True), True))
            _mask_range(masked, index, end)
            index = end
            continue
        if source[index] == '"':
            end = index + 1
            escaped = False
            while end < len(source):
                char = source[end]
                if char == '"' and not escaped:
                    end += 1
                    break
                if char == "\n" and not escaped:
                    errors.append(f"Unterminated string near line {_line(newlines, index)}")
                    break
                escaped = char == "\\" and not escaped
                if char != "\\":
                    escaped = False
                end += 1
            else:
                errors.append(f"Unterminated string near line {_line(newlines, index)}")
            raw = source[index:end]
            if raw.endswith('"'):
                tokens.append(_LiteralToken(index, end, _decode_java_string(raw, False), False))
            _mask_range(masked, index, end)
            index = end
            continue
        if source[index] == "'":
            end = index + 1
            escaped = False
            while end < len(source):
                char = source[end]
                if char == "'" and not escaped:
                    end += 1
                    break
                escaped = char == "\\" and not escaped
                if char != "\\":
                    escaped = False
                end += 1
            _mask_range(masked, index, end)
            index = end
            continue
        index += 1
    return "".join(masked), tokens, "; ".join(errors) if errors else None


def _find_matching_brace(masked: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(masked)):
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _annotations(raw: str) -> tuple[str, ...]:
    return tuple(match.group(0).strip() for match in re.finditer(_ANNOTATION, raw))


def _modifiers(raw: str) -> tuple[str, ...]:
    return tuple(part for part in raw.split() if part)


def _base_types(tail: str) -> tuple[str, ...]:
    match = re.search(r"\b(?:extends|implements|permits)\s+(.+)", tail)
    if not match:
        return ()
    value = re.split(r"\b(?:implements|permits)\b", match.group(1))[0]
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _extract_imports(masked: str, newlines: list[int]) -> list[JavaImport]:
    return [
        JavaImport(
            module=match.group("module"),
            is_static=bool(match.group("static")),
            wildcard=bool(match.group("wildcard")),
            line=_line(newlines, match.start()),
        )
        for match in _IMPORT_RE.finditer(masked)
    ]


def _extract_types(
    source: str, masked: str, newlines: list[int]
) -> tuple[list[JavaTypeDeclaration], list[_Span]]:
    declarations: list[JavaTypeDeclaration] = []
    spans: list[_Span] = []
    for match in _TYPE_RE.finditer(masked):
        opening = match.end() - 1
        closing = _find_matching_brace(masked, opening)
        if closing is None:
            continue
        name = match.group("name")
        start = match.start()
        spans.append(_Span(name=name, start=start, body_start=opening, end=closing))
        declarations.append(
            JavaTypeDeclaration(
                name=name,
                kind=match.group("kind"),
                annotations=_annotations(
                    source[match.start("annotations") : match.end("annotations")]
                ),
                modifiers=_modifiers(match.group("modifiers")),
                base_types=_base_types(match.group("tail")),
                line=_line(newlines, start),
                line_end=_line(newlines, closing),
                signature=source[start : opening + 1].strip(),
                body_start=opening,
                body_end=closing,
            )
        )
    return declarations, spans


def _containing_type(type_spans: list[_Span], position: int) -> _Span | None:
    candidates = [span for span in type_spans if span.body_start < position < span.end]
    return min(candidates, key=lambda span: span.end - span.start) if candidates else None


def _split_parameters(raw: str) -> tuple[str, ...]:
    result: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(raw):
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            value = raw[start:index].strip()
            if value:
                result.append(value)
            start = index + 1
    value = raw[start:].strip()
    if value:
        result.append(value)
    return tuple(result)


def _extract_methods(
    source: str,
    masked: str,
    newlines: list[int],
    type_spans: list[_Span],
    type_names: set[str],
) -> tuple[list[JavaMethodDeclaration], list[_Span]]:
    methods: list[JavaMethodDeclaration] = []
    spans: list[_Span] = []
    for match in _METHOD_RE.finditer(masked):
        name = match.group("name")
        if name in _NON_METHOD_NAMES:
            continue
        owner = _containing_type(type_spans, match.start())
        if owner is None:
            continue
        terminator = match.group("terminator")
        opening = match.end() - 1 if terminator == "{" else match.end()
        closing = _find_matching_brace(masked, opening) if terminator == "{" else match.end()
        if closing is None:
            continue
        return_type = (match.group("return") or "").strip() or None
        is_constructor = name in type_names and name == owner.name
        if is_constructor:
            return_type = None
        start = match.start()
        body = source[opening + 1 : closing] if terminator == "{" else ""
        methods.append(
            JavaMethodDeclaration(
                name=name,
                return_type=return_type,
                parameters=_split_parameters(match.group("params")),
                annotations=_annotations(
                    source[match.start("annotations") : match.end("annotations")]
                ),
                modifiers=_modifiers(match.group("modifiers")),
                containing_type=owner.name,
                is_constructor=is_constructor,
                line=_line(newlines, start),
                line_end=_line(newlines, closing),
                signature=source[start : opening + 1].strip(),
                body=body,
                body_start=opening,
                body_end=closing,
            )
        )
        spans.append(_Span(name=name, start=start, body_start=opening, end=closing))
    return methods, spans


def _assignment_name(source: str, position: int) -> str | None:
    line_start = source.rfind("\n", 0, position) + 1
    prefix = source[line_start:position]
    match = re.search(r"(?P<name>[A-Za-z_$][\w$]*)\s*=\s*$", prefix)
    return match.group("name") if match else None


def _enclosing_method(method_spans: list[_Span], position: int) -> str | None:
    candidates = [span for span in method_spans if span.body_start < position < span.end]
    return min(candidates, key=lambda span: span.end - span.start).name if candidates else None


def _build_literals(
    source: str,
    masked: str,
    tokens: list[_LiteralToken],
    newlines: list[int],
    method_spans: list[_Span],
) -> list[JavaStringLiteral]:
    del masked
    return [
        JavaStringLiteral(
            value=token.value,
            line=_line(newlines, token.start),
            line_end=_line(newlines, max(token.start, token.end - 1)),
            assigned_to=_assignment_name(source, token.start),
            enclosing_method=_enclosing_method(method_spans, token.start),
            is_text_block=token.is_text_block,
        )
        for token in tokens
    ]


def _brace_error(masked: str, newlines: list[int]) -> str | None:
    stack: list[int] = []
    for index, char in enumerate(masked):
        if char == "{":
            stack.append(index)
        elif char == "}":
            if not stack:
                return f"Unexpected closing brace near line {_line(newlines, index)}"
            stack.pop()
    if stack:
        return f"Unclosed brace near line {_line(newlines, stack[-1])}"
    return None
