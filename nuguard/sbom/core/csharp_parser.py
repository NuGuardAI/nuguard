"""Dependency-free structural parsing for C# source files.

The parser is intentionally best-effort. It extracts the source structures
needed by C# framework adapters without attempting full compiler semantics.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CSharpUsingDirective:
    """One C# ``using`` directive."""

    namespace: str
    alias: str | None = None
    is_static: bool = False
    is_global: bool = False
    line: int = 0

    @property
    def module(self) -> str:
        return self.namespace


@dataclass(frozen=True)
class CSharpTypeDeclaration:
    """A class, record, struct, or interface declaration."""

    name: str
    kind: str
    modifiers: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    base_types: tuple[str, ...] = ()
    line: int = 0
    line_end: int = 0
    signature: str = ""


@dataclass(frozen=True)
class CSharpMethodDeclaration:
    """A method or constructor declaration."""

    name: str
    return_type: str | None
    parameters: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    containing_type: str | None = None
    is_constructor: bool = False
    line: int = 0
    line_end: int = 0
    signature: str = ""


@dataclass(frozen=True)
class CSharpStringLiteral:
    """A regular, verbatim, interpolated, or raw string literal."""

    value: str
    line: int
    line_end: int
    assigned_to: str | None = None
    enclosing_method: str | None = None
    is_verbatim: bool = False
    is_interpolated: bool = False
    is_raw: bool = False
    interpolation_expressions: tuple[str, ...] = ()

    @property
    def is_potential_prompt(self) -> bool:
        lowered = self.value.lower()
        return len(self.value) > 100 or any(
            marker in lowered
            for marker in (
                "you are",
                "system:",
                "assistant:",
                "user:",
                "instructions:",
            )
        )


@dataclass
class CSharpParseResult:
    """Structural information extracted from one C# source file."""

    using_directives: list[CSharpUsingDirective] = field(default_factory=list)
    type_declarations: list[CSharpTypeDeclaration] = field(default_factory=list)
    method_declarations: list[CSharpMethodDeclaration] = field(default_factory=list)
    string_literals: list[CSharpStringLiteral] = field(default_factory=list)
    source: str = ""
    file_path: str = ""
    parse_error: str | None = None

    @property
    def imports(self) -> list[CSharpUsingDirective]:
        return self.using_directives

    @property
    def classes(self) -> list[CSharpTypeDeclaration]:
        return self.type_declarations

    @property
    def methods(self) -> list[CSharpMethodDeclaration]:
        return self.method_declarations

    def __bool__(self) -> bool:
        return bool(
            self.using_directives
            or self.type_declarations
            or self.method_declarations
            or self.string_literals
        )


@dataclass(frozen=True)
class _LiteralToken:
    start: int
    end: int
    value: str
    is_verbatim: bool
    is_interpolated: bool
    is_raw: bool
    expressions: tuple[str, ...]


@dataclass(frozen=True)
class _Span:
    name: str
    start: int
    body_start: int
    end: int


_IDENTIFIER = r"@?[A-Za-z_]\w*"

_TOKEN_RE = re.compile(
    r"(?P<line_comment>//[^\n]*)"
    r"|(?P<block_comment>/\*.*?\*/)"
    r'|(?P<raw>\$*""".*?""")'
    r'|(?P<verbatim>(?:\$@|@\$|@)"(?:""|[^"])*")'
    r'|(?P<regular>\$?"(?:\\.|[^"\\\r\n])*")'
    r"|(?P<char>'(?:\\.|[^'\\\r\n])')",
    re.DOTALL,
)

_USING_RE = re.compile(
    rf"(?m)^[ \t]*(?P<global>global\s+)?using\s+"
    rf"(?:(?P<static>static)\s+)?"
    rf"(?:(?P<alias>{_IDENTIFIER})\s*=\s*)?"
    rf"(?P<namespace>(?:global::)?{_IDENTIFIER}"
    rf"(?:\.{_IDENTIFIER})*)\s*;"
)

_TYPE_RE = re.compile(
    rf"(?m)^[ \t]*"
    rf"(?P<attributes>(?:(?:\[[^\]\n]+\])[ \t\r\n]*)*)"
    rf"(?P<modifiers>(?:(?:public|private|protected|internal|abstract|"
    rf"sealed|static|partial|readonly|ref|unsafe|new|file)\s+)*)"
    rf"(?P<kind>record(?:\s+(?:class|struct))?|class|struct|interface)\s+"
    rf"(?P<name>{_IDENTIFIER})(?:\s*<[^>{{}};\n]+>)?"
    rf"(?P<tail>\s*(?:\([^{{}};]*\))?\s*"
    rf"(?::[^{{}};]+)?(?:\s+where\s+[^{{}};]+)*)"
    rf"\s*(?P<terminator>{{|;)"
)

_METHOD_RE = re.compile(
    rf"(?m)(?P<start>^[ \t]*|(?<=[{{}};]))"
    rf"(?P<attributes>(?:(?:\[[^\]\n]+\])[ \t\r\n]*)*)"
    rf"(?P<modifiers>(?:(?:public|private|protected|internal|static|"
    rf"abstract|virtual|override|sealed|new|async|extern|unsafe|partial|"
    rf"readonly|ref|required)\s+)*)"
    rf"(?:(?P<return>[A-Za-z_][\w.<>,?\[\]\s:]*)\s+)?"
    rf"(?P<name>{_IDENTIFIER})(?:\s*<[^>{{}};()\n]+>)?\s*"
    rf"\((?P<params>[^()]*)\)\s*"
    rf"(?:(?:where\s+[^{{}};=]+)\s*)?"
    rf"(?P<terminator>{{|=>|;)"
)

_NON_METHOD_NAMES = {
    "if",
    "for",
    "foreach",
    "while",
    "switch",
    "catch",
    "using",
    "lock",
    "return",
    "throw",
    "new",
    "typeof",
    "nameof",
    "sizeof",
    "default",
}


def parse_csharp(
    content: str,
    file_path: str = "",
) -> CSharpParseResult:
    """Parse C# source into a best-effort structural result."""
    newlines = [match.start() for match in re.finditer(r"\n", content)]

    masked, literal_tokens, lexical_error = _mask_non_code(
        content,
        newlines,
    )

    types, type_spans, type_headers = _extract_types(
        content,
        masked,
        newlines,
    )

    methods, method_spans = _extract_methods(
        content,
        masked,
        newlines,
        type_spans,
        type_headers,
    )

    errors = [
        error
        for error in (
            lexical_error,
            _brace_error(masked, newlines),
        )
        if error
    ]

    return CSharpParseResult(
        using_directives=_extract_usings(
            masked,
            newlines,
        ),
        type_declarations=types,
        method_declarations=methods,
        string_literals=_build_literals(
            content,
            literal_tokens,
            newlines,
            method_spans,
        ),
        source=content,
        file_path=file_path,
        parse_error="; ".join(errors) if errors else None,
    )


def _line(
    newlines: list[int],
    position: int,
) -> int:
    return bisect_right(newlines, position) + 1


def _mask_non_code(
    source: str,
    newlines: list[int],
) -> tuple[str, list[_LiteralToken], str | None]:
    masked = list(source)
    tokens: list[_LiteralToken] = []

    for match in _TOKEN_RE.finditer(source):
        for index in range(
            match.start(),
            match.end(),
        ):
            if masked[index] not in {"\n", "\r"}:
                masked[index] = " "

        kind = match.lastgroup

        if kind in {
            "line_comment",
            "block_comment",
            "char",
        }:
            continue

        raw = match.group(0)
        is_raw = kind == "raw"
        is_verbatim = kind == "verbatim"
        is_interpolated = raw.startswith("$") or raw.startswith("@$")

        if is_raw:
            dollars = len(raw) - len(raw.lstrip("$"))
            value = raw[dollars + 3 : -3]
            braces = max(dollars, 1)
        else:
            quote = raw.find('"')
            value = raw[quote + 1 : -1]

            if is_verbatim:
                value = value.replace('""', '"')
            else:
                value = _decode_escapes(value)

            braces = 1

        tokens.append(
            _LiteralToken(
                start=match.start(),
                end=match.end(),
                value=value,
                is_verbatim=is_verbatim,
                is_interpolated=is_interpolated,
                is_raw=is_raw,
                expressions=(_interpolations(value, braces) if is_interpolated else ()),
            )
        )

    result = "".join(masked)
    unterminated = result.find("/*")

    error = (
        f"Unterminated block comment near line {_line(newlines, unterminated)}"
        if unterminated >= 0
        else None
    )

    return result, tokens, error


def _decode_escapes(value: str) -> str:
    replacements = {
        r"\0": "\0",
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
        r"\"": '"',
        r"\\": "\\",
    }

    return re.sub(
        r"\\(?:0|n|r|t|\"|\\)",
        lambda match: replacements.get(
            match.group(0),
            match.group(0),
        ),
        value,
    )


def _interpolations(
    value: str,
    brace_count: int,
) -> tuple[str, ...]:
    opening = "{" * brace_count
    closing = "}" * brace_count

    pattern = re.compile(
        rf"(?<!\{{){re.escape(opening)}\s*"
        rf"([^{{}}]+?)\s*"
        rf"{re.escape(closing)}(?!\}})"
    )

    return tuple(dict.fromkeys(match.group(1).strip() for match in pattern.finditer(value)))


def _brace_error(
    masked: str,
    newlines: list[int],
) -> str | None:
    stack: list[int] = []

    for index, char in enumerate(masked):
        if char == "{":
            stack.append(index)
        elif char == "}":
            if not stack:
                return f"Unexpected closing brace near line {_line(newlines, index)}"

            stack.pop()

    if stack:
        return f"Unmatched opening brace near line {_line(newlines, stack[-1])}"

    return None


def _extract_usings(
    masked: str,
    newlines: list[int],
) -> list[CSharpUsingDirective]:
    return [
        CSharpUsingDirective(
            namespace=match.group("namespace").removeprefix("global::"),
            alias=match.group("alias"),
            is_static=bool(match.group("static")),
            is_global=bool(match.group("global")),
            line=_line(
                newlines,
                match.start(),
            ),
        )
        for match in _USING_RE.finditer(masked)
    ]


def _extract_types(
    source: str,
    masked: str,
    newlines: list[int],
) -> tuple[
    list[CSharpTypeDeclaration],
    list[_Span],
    list[tuple[int, int]],
]:
    declarations: list[CSharpTypeDeclaration] = []
    spans: list[_Span] = []
    headers: list[tuple[int, int]] = []

    for match in _TYPE_RE.finditer(masked):
        body_start = match.start("terminator")
        end = match.end("terminator")

        if match.group("terminator") == "{":
            closing = _matching(
                masked,
                body_start,
                "{",
                "}",
            )

            end = len(source) if closing is None else closing + 1

        name = match.group("name").removeprefix("@")

        declaration = CSharpTypeDeclaration(
            name=name,
            kind=" ".join(match.group("kind").split()),
            modifiers=tuple((match.group("modifiers") or "").split()),
            attributes=_attributes(source[match.start("attributes") : match.end("attributes")]),
            base_types=_base_types(match.group("tail") or ""),
            line=_line(
                newlines,
                match.start("name"),
            ),
            line_end=_line(
                newlines,
                max(
                    end - 1,
                    match.start(),
                ),
            ),
            signature=source[match.start() : body_start + 1].strip(),
        )

        declarations.append(declaration)

        spans.append(
            _Span(
                name,
                match.start(),
                body_start,
                end,
            )
        )

        headers.append(
            (
                match.start(),
                body_start + 1,
            )
        )

    return declarations, spans, headers


def _extract_methods(
    source: str,
    masked: str,
    newlines: list[int],
    type_spans: list[_Span],
    type_headers: list[tuple[int, int]],
) -> tuple[
    list[CSharpMethodDeclaration],
    list[_Span],
]:
    declarations: list[CSharpMethodDeclaration] = []
    spans: list[_Span] = []

    for match in _METHOD_RE.finditer(masked):
        if any(start <= match.start("name") < end for start, end in type_headers):
            continue

        name = match.group("name").removeprefix("@")

        if name in _NON_METHOD_NAMES:
            continue

        containing = _containing(
            type_spans,
            match.start("name"),
        )

        return_type = (match.group("return") or "").strip() or None

        is_constructor = bool(containing and containing.name == name and return_type is None)

        if return_type is None and not is_constructor:
            continue

        body_start = match.start("terminator")
        end = match.end("terminator")

        if match.group("terminator") == "{":
            closing = _matching(
                masked,
                body_start,
                "{",
                "}",
            )

            end = len(source) if closing is None else closing + 1

        elif match.group("terminator") == "=>":
            semicolon = masked.find(
                ";",
                body_start,
            )

            end = len(source) if semicolon < 0 else semicolon + 1

        declaration = CSharpMethodDeclaration(
            name=name,
            return_type=return_type,
            parameters=tuple(
                part.strip() for part in _split(match.group("params")) if part.strip()
            ),
            modifiers=tuple((match.group("modifiers") or "").split()),
            attributes=_attributes(source[match.start("attributes") : match.end("attributes")]),
            containing_type=(containing.name if containing else None),
            is_constructor=is_constructor,
            line=_line(
                newlines,
                match.start("name"),
            ),
            line_end=_line(
                newlines,
                max(
                    end - 1,
                    match.start(),
                ),
            ),
            signature=source[match.start() : match.end()].strip(),
        )

        declarations.append(declaration)

        spans.append(
            _Span(
                name,
                match.start(),
                body_start,
                end,
            )
        )

    return declarations, spans


def _matching(
    source: str,
    start: int,
    opening: str,
    closing: str,
) -> int | None:
    depth = 0

    for index in range(
        start,
        len(source),
    ):
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1

            if depth == 0:
                return index

    return None


def _containing(
    spans: list[_Span],
    position: int,
) -> _Span | None:
    matches = [span for span in spans if span.body_start < position < span.end]

    return (
        min(
            matches,
            key=lambda span: span.end - span.start,
        )
        if matches
        else None
    )


def _attributes(raw: str) -> tuple[str, ...]:
    return tuple(
        match.group(1).strip()
        for match in re.finditer(
            r"\[([^\]]+)\]",
            raw,
        )
    )


def _base_types(
    tail: str,
) -> tuple[str, ...]:
    if ":" not in tail:
        return ()

    value = re.split(
        r"\bwhere\b",
        tail.split(":", 1)[1],
        maxsplit=1,
    )[0]

    return tuple(part.strip() for part in _split(value) if part.strip())


def _split(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False

    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(
                depth - 1,
                0,
            )
        elif char == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1

    parts.append(value[start:])

    return parts


def _build_literals(
    source: str,
    tokens: list[_LiteralToken],
    newlines: list[int],
    method_spans: list[_Span],
) -> list[CSharpStringLiteral]:
    literals: list[CSharpStringLiteral] = []

    for token in tokens:
        method = _containing(
            method_spans,
            token.start,
        )

        literals.append(
            CSharpStringLiteral(
                value=token.value,
                line=_line(
                    newlines,
                    token.start,
                ),
                line_end=_line(
                    newlines,
                    token.end - 1,
                ),
                assigned_to=_assignment_target(
                    source,
                    token.start,
                ),
                enclosing_method=(method.name if method else None),
                is_verbatim=token.is_verbatim,
                is_interpolated=token.is_interpolated,
                is_raw=token.is_raw,
                interpolation_expressions=token.expressions,
            )
        )

    return literals


def _assignment_target(
    source: str,
    position: int,
) -> str | None:
    prefix = source[
        source.rfind(
            "\n",
            0,
            position,
        )
        + 1 : position
    ]

    match = re.search(
        rf"(?P<name>{_IDENTIFIER})\s*=\s*$",
        prefix,
    )

    return match.group("name").removeprefix("@") if match else None


__all__ = [
    "CSharpMethodDeclaration",
    "CSharpParseResult",
    "CSharpStringLiteral",
    "CSharpTypeDeclaration",
    "CSharpUsingDirective",
    "parse_csharp",
]
