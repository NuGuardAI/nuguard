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

_TYPE_TOKEN = (
    rf"(?:global::)?{_IDENTIFIER}"
    rf"(?:\.{_IDENTIFIER})*"
    rf"(?:\s*<[^;{{}}()\n]+>)?"
    rf"(?:\s*\[\s*\])*\??"
)

_METHOD_HEAD_RE = re.compile(
    rf"(?m)(?P<start>^[ \t]*|(?<=[{{}};])[ \t]*)"
    rf"(?P<attributes>(?:(?:\[[^\]\n]+\])[ \t\r\n]*)*)"
    rf"(?P<modifiers>(?:(?:public|private|protected|internal|static|"
    rf"abstract|virtual|override|sealed|new|async|extern|unsafe|partial|"
    rf"readonly|ref|required)\s+)*)"
    rf"(?:(?P<return>{_TYPE_TOKEN})\s+)?"
    rf"(?P<name>{_IDENTIFIER})"
    rf"(?:\s*<[^>{{}};()\n]+>)?\s*"
    rf"(?P<open>\()"
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
            masked,
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
    """Mask comments, strings, and characters while preserving offsets."""
    masked = list(source)
    tokens: list[_LiteralToken] = []
    errors: list[str] = []
    index = 0
    end: int | None
    closing: int | None

    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index)

            if end < 0:
                end = len(source)

            _mask_range(
                masked,
                index,
                end,
            )
            index = end
            continue

        if source.startswith("/*", index):
            closing = source.find(
                "*/",
                index + 2,
            )

            if closing < 0:
                _mask_range(
                    masked,
                    index,
                    len(source),
                )
                errors.append(f"Unterminated block comment near line {_line(newlines, index)}")
                break

            end = closing + 2
            _mask_range(
                masked,
                index,
                end,
            )
            index = end
            continue

        raw_opener = _raw_string_opener(
            source,
            index,
        )

        if raw_opener is not None:
            (
                dollar_count,
                quote_count,
                content_start,
            ) = raw_opener

            closing = _raw_string_close(
                source,
                content_start,
                quote_count,
            )

            if closing is None:
                _mask_range(
                    masked,
                    index,
                    len(source),
                )
                errors.append(f"Unterminated raw string near line {_line(newlines, index)}")
                break

            end = closing + quote_count
            value = source[content_start:closing]
            is_interpolated = dollar_count > 0

            _mask_range(
                masked,
                index,
                end,
            )

            tokens.append(
                _LiteralToken(
                    start=index,
                    end=end,
                    value=value,
                    is_verbatim=False,
                    is_interpolated=(is_interpolated),
                    is_raw=True,
                    expressions=(
                        _interpolations(
                            value,
                            max(
                                dollar_count,
                                1,
                            ),
                        )
                        if is_interpolated
                        else ()
                    ),
                )
            )

            index = end
            continue

        quoted_prefix = _quoted_string_prefix(
            source,
            index,
        )

        if quoted_prefix is not None:
            (
                prefix,
                is_verbatim,
                is_interpolated,
            ) = quoted_prefix
            body_start = index + len(prefix)
            end = _quoted_string_end(
                source,
                body_start,
                is_verbatim,
            )

            if end is None:
                _mask_range(
                    masked,
                    index,
                    len(source),
                )
                string_kind = "verbatim" if is_verbatim else "regular"
                errors.append(
                    f"Unterminated {string_kind} string near line {_line(newlines, index)}"
                )
                break

            value = source[body_start : end - 1]

            if is_verbatim:
                value = value.replace(
                    '""',
                    '"',
                )
            else:
                value = _decode_escapes(value)

            _mask_range(
                masked,
                index,
                end,
            )

            tokens.append(
                _LiteralToken(
                    start=index,
                    end=end,
                    value=value,
                    is_verbatim=is_verbatim,
                    is_interpolated=(is_interpolated),
                    is_raw=False,
                    expressions=(
                        _interpolations(
                            value,
                            1,
                        )
                        if is_interpolated
                        else ()
                    ),
                )
            )

            index = end
            continue

        if source[index] == "'":
            end = _character_literal_end(
                source,
                index,
            )

            if end is None:
                _mask_range(
                    masked,
                    index,
                    len(source),
                )
                errors.append(f"Unterminated character literal near line {_line(newlines, index)}")
                break

            _mask_range(
                masked,
                index,
                end,
            )
            index = end
            continue

        index += 1

    return (
        "".join(masked),
        tokens,
        "; ".join(errors) if errors else None,
    )


def _mask_range(
    masked: list[str],
    start: int,
    end: int,
) -> None:
    """Mask a source range without changing line positions."""
    for index in range(start, end):
        if masked[index] not in {
            "\n",
            "\r",
        }:
            masked[index] = " "


def _raw_string_opener(
    source: str,
    start: int,
) -> tuple[int, int, int] | None:
    """Return dollar count, quote count, and content start."""
    cursor = start

    while cursor < len(source) and source[cursor] == "$":
        cursor += 1

    quote_start = cursor

    while cursor < len(source) and source[cursor] == '"':
        cursor += 1

    quote_count = cursor - quote_start

    if quote_count < 3:
        return None

    return (
        quote_start - start,
        quote_count,
        cursor,
    )


def _raw_string_close(
    source: str,
    start: int,
    quote_count: int,
) -> int | None:
    """Find a raw-string closing delimiter of the same length."""
    cursor = start

    while cursor < len(source):
        if source[cursor] != '"':
            cursor += 1
            continue

        run_start = cursor

        while cursor < len(source) and source[cursor] == '"':
            cursor += 1

        if cursor - run_start == quote_count:
            return run_start

    return None


def _quoted_string_prefix(
    source: str,
    start: int,
) -> tuple[str, bool, bool] | None:
    """Return prefix, verbatim flag, and interpolation flag."""
    prefixes = (
        (
            '$@"',
            True,
            True,
        ),
        (
            '@$"',
            True,
            True,
        ),
        (
            '@"',
            True,
            False,
        ),
        (
            '$"',
            False,
            True,
        ),
        (
            '"',
            False,
            False,
        ),
    )

    for (
        prefix,
        is_verbatim,
        is_interpolated,
    ) in prefixes:
        if source.startswith(
            prefix,
            start,
        ):
            return (
                prefix,
                is_verbatim,
                is_interpolated,
            )

    return None


def _quoted_string_end(
    source: str,
    start: int,
    is_verbatim: bool,
) -> int | None:
    """Return the offset immediately after a closing quote."""
    cursor = start

    while cursor < len(source):
        char = source[cursor]

        if is_verbatim:
            if char == '"':
                if cursor + 1 < len(source) and source[cursor + 1] == '"':
                    cursor += 2
                    continue

                return cursor + 1

            cursor += 1
            continue

        if char == "\\":
            if cursor + 1 >= len(source) or source[cursor + 1] in {"\n", "\r"}:
                return None

            cursor += 2
            continue

        if char == '"':
            return cursor + 1

        if char in {
            "\n",
            "\r",
        }:
            return None

        cursor += 1

    return None


def _character_literal_end(
    source: str,
    start: int,
) -> int | None:
    """Return the offset immediately after a character literal."""
    cursor = start + 1

    while cursor < len(source):
        char = source[cursor]

        if char == "\\":
            if cursor + 1 >= len(source):
                return None

            cursor += 2
            continue

        if char == "'":
            return cursor + 1

        if char in {
            "\n",
            "\r",
        }:
            return None

        cursor += 1

    return None


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

    for match in _METHOD_HEAD_RE.finditer(masked):
        name_position = match.start("name")

        if any(start <= name_position < end for start, end in type_headers):
            continue

        # Once a method has been found, do not reinterpret calls or
        # statements inside its body as additional declarations.
        if (
            _containing(
                spans,
                name_position,
            )
            is not None
        ):
            continue

        containing = _containing(
            type_spans,
            name_position,
        )

        if containing is None:
            continue

        name = match.group("name").removeprefix("@")

        if name in _NON_METHOD_NAMES:
            continue

        return_type = (match.group("return") or "").strip() or None

        if return_type is not None and return_type.casefold() in {
            "return",
            "throw",
            "yield",
            "await",
        }:
            continue

        is_constructor = bool(containing.name == name and return_type is None)

        if return_type is None and not is_constructor:
            continue

        open_paren = match.start("open")
        close_paren = _matching(
            masked,
            open_paren,
            "(",
            ")",
        )

        if close_paren is None:
            continue

        terminator_info = _method_terminator(
            masked,
            close_paren + 1,
        )

        if terminator_info is None:
            continue

        (
            body_start,
            terminator,
        ) = terminator_info
        terminator_end = body_start + len(terminator)
        end = terminator_end

        if terminator == "{":
            closing = _matching(
                masked,
                body_start,
                "{",
                "}",
            )

            end = len(source) if closing is None else closing + 1

        elif terminator == "=>":
            semicolon = masked.find(
                ";",
                terminator_end,
            )

            end = len(source) if semicolon < 0 else semicolon + 1

        parameters_text = source[open_paren + 1 : close_paren]

        declaration = CSharpMethodDeclaration(
            name=name,
            return_type=return_type,
            parameters=tuple(part.strip() for part in _split(parameters_text) if part.strip()),
            modifiers=tuple((match.group("modifiers") or "").split()),
            attributes=_attributes(source[match.start("attributes") : match.end("attributes")]),
            containing_type=(containing.name),
            is_constructor=is_constructor,
            line=_line(
                newlines,
                name_position,
            ),
            line_end=_line(
                newlines,
                max(
                    end - 1,
                    match.start(),
                ),
            ),
            signature=source[match.start() : terminator_end].strip(),
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


def _method_terminator(
    source: str,
    start: int,
) -> tuple[int, str] | None:
    """Find a declaration terminator after a balanced parameter list."""
    depths = {
        "(": 0,
        "[": 0,
        "<": 0,
    }
    closing = {
        ")": "(",
        "]": "[",
        ">": "<",
    }
    cursor = start

    while cursor < len(source):
        if not any(depths.values()) and source.startswith(
            "=>",
            cursor,
        ):
            return cursor, "=>"

        char = source[cursor]

        if not any(depths.values()):
            if char == "{":
                return cursor, "{"

            if char == ";":
                return cursor, ";"

            if char == "}":
                return None

        if char in depths:
            depths[char] += 1
        elif char in closing:
            opener = closing[char]
            depths[opener] = max(
                depths[opener] - 1,
                0,
            )

        cursor += 1

    return None


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
    masked: str,
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
                assigned_to=(
                    _assignment_target(
                        source,
                        masked,
                        token.start,
                    )
                ),
                enclosing_method=(method.name if method else None),
                is_verbatim=(token.is_verbatim),
                is_interpolated=(token.is_interpolated),
                is_raw=token.is_raw,
                interpolation_expressions=(token.expressions),
            )
        )

    return literals


def _assignment_target(
    source: str,
    masked: str,
    position: int,
) -> str | None:
    """Return the assignment target before a string literal."""
    statement_start = (
        max(
            masked.rfind(
                ";",
                0,
                position,
            ),
            masked.rfind(
                "{",
                0,
                position,
            ),
            masked.rfind(
                "}",
                0,
                position,
            ),
        )
        + 1
    )

    prefix = masked[statement_start:position]

    match = re.search(
        rf"(?P<name>{_IDENTIFIER})"
        rf"\s*=\s*$",
        prefix,
    )

    if match is None:
        return None

    # Access source so this helper continues to validate compatible offsets.
    if len(source) != len(masked):
        return None

    return match.group("name").removeprefix("@")


__all__ = [
    "CSharpMethodDeclaration",
    "CSharpParseResult",
    "CSharpStringLiteral",
    "CSharpTypeDeclaration",
    "CSharpUsingDirective",
    "parse_csharp",
]
