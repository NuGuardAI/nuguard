"""Small source helpers shared by C# framework adapters."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Iterable

from ...core.csharp_parser import CSharpMethodDeclaration, CSharpParseResult

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
_GENERIC = r"(?:\s*<[^<>{}();\n]+>)?"
_CALL_RE = re.compile(
    rf"(?P<callee>(?:global::)?{_IDENTIFIER}{_GENERIC}"
    rf"(?:\s*\.\s*{_IDENTIFIER}{_GENERIC})*)\s*\("
)
_CONTROL_NAMES = {
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
    "typeof",
    "nameof",
    "sizeof",
    "default",
    "checked",
    "unchecked",
}


@dataclass(frozen=True)
class CSharpCall:
    """One best-effort C# call or constructor expression."""

    callee: str
    name: str
    receiver: str | None
    generic_arguments: tuple[str, ...]
    arguments_text: str
    named_arguments: dict[str, str]
    positional_arguments: tuple[str, ...]
    assigned_to: str | None
    is_constructor: bool
    line: int
    start: int
    end: int
    snippet: str


def mask_non_code(source: str) -> str:
    """Mask comments, strings, and character literals without shifting offsets."""
    masked = list(source)

    for match in _TOKEN_RE.finditer(source):
        for index in range(match.start(), match.end()):
            if masked[index] not in {"\n", "\r"}:
                masked[index] = " "

    return "".join(masked)


def line_number(source: str, position: int) -> int:
    offsets = [match.start() for match in re.finditer(r"\n", source)]
    return bisect_right(offsets, position) + 1


def find_calls(
    source: str,
    names: set[str] | None = None,
) -> list[CSharpCall]:
    """Return call expressions whose simple name is in *names*, if supplied."""
    masked = mask_non_code(source)
    calls: list[CSharpCall] = []

    for match in _CALL_RE.finditer(masked):
        callee = re.sub(
            r"\s+",
            "",
            match.group("callee"),
        )
        simple_parts = _split_callee(callee)

        if not simple_parts:
            continue

        name, generic_arguments = _split_generic(simple_parts[-1])
        name = name.removeprefix("@")

        if name in _CONTROL_NAMES:
            continue

        if names is not None and name not in names:
            continue

        open_paren = match.end() - 1
        close_paren = _matching(
            masked,
            open_paren,
            "(",
            ")",
        )

        if close_paren is None:
            continue

        args_text = source[open_paren + 1 : close_paren]
        named, positional = parse_arguments(args_text)
        receiver = ".".join(simple_parts[:-1]) or None
        prefix = masked[max(0, match.start() - 12) : match.start()]
        is_constructor = bool(re.search(r"\bnew\s*$", prefix))
        assigned_to = _assignment_target(
            source,
            match.start(),
        )
        end = close_paren + 1
        snippet = " ".join(source[match.start() : end].strip().split())[:240]

        calls.append(
            CSharpCall(
                callee=callee,
                name=name,
                receiver=receiver,
                generic_arguments=generic_arguments,
                arguments_text=args_text,
                named_arguments=named,
                positional_arguments=tuple(positional),
                assigned_to=assigned_to,
                is_constructor=is_constructor,
                line=line_number(
                    source,
                    match.start(),
                ),
                start=match.start(),
                end=end,
                snippet=snippet,
            )
        )

    return calls


def parse_arguments(
    value: str,
) -> tuple[dict[str, str], list[str]]:
    """Split C# call arguments into named and positional expressions."""
    named: dict[str, str] = {}
    positional: list[str] = []

    for part in split_top_level(value):
        item = part.strip()

        if not item:
            continue

        name, expression = _named_argument(item)

        if name is None:
            positional.append(item)
        else:
            named[name] = expression

    return named, positional


def split_top_level(value: str) -> list[str]:
    """Split comma-delimited C# text while respecting nesting and strings."""
    parts: list[str] = []
    start = 0
    depths = {
        "(": 0,
        "[": 0,
        "{": 0,
        "<": 0,
    }
    closing = {
        ")": "(",
        "]": "[",
        "}": "{",
        ">": "<",
    }
    index = 0
    quote: str | None = None
    verbatim = False
    raw = False

    while index < len(value):
        if raw:
            if value.startswith('"""', index):
                raw = False
                index += 3
                continue

            index += 1
            continue

        char = value[index]

        if quote:
            if verbatim:
                if char == '"' and index + 1 < len(value) and value[index + 1] == '"':
                    index += 2
                    continue

                if char == '"':
                    quote = None
                    verbatim = False
            else:
                if char == "\\":
                    index += 2
                    continue

                if char == quote:
                    quote = None

            index += 1
            continue

        if value.startswith('"""', index):
            raw = True
            index += 3
            continue

        if char == '"':
            quote = char
            verbatim = index > 0 and value[index - 1] == "@"
            index += 1
            continue

        if char == "'":
            quote = char
            index += 1
            continue

        if char in depths:
            depths[char] += 1
        elif char in closing:
            opener = closing[char]
            depths[opener] = max(
                depths[opener] - 1,
                0,
            )
        elif char == "," and not any(depths.values()):
            parts.append(value[start:index])
            start = index + 1

        index += 1

    parts.append(value[start:])
    return parts


def string_constants(
    result: CSharpParseResult,
) -> dict[str, str]:
    """Build a variable-to-string map from parsed literal results."""
    return {
        literal.assigned_to: literal.value
        for literal in result.string_literals
        if literal.assigned_to and literal.value
    }


def resolve_expression(
    expression: str | None,
    constants: dict[str, str],
) -> str:
    """Resolve a simple C# string, constant, enum member, or nameof value."""
    if not expression:
        return ""

    value = expression.strip()

    while value.startswith("(") and value.endswith(")") and _balanced_outer(value):
        value = value[1:-1].strip()

    named = re.fullmatch(
        r"nameof\s*\(\s*([^)]+)\s*\)",
        value,
    )

    if named:
        return named.group(1).split(".")[-1].strip()

    uri = re.fullmatch(
        r"new\s+Uri\s*\((.*)\)",
        value,
        re.DOTALL,
    )

    if uri:
        return resolve_expression(
            uri.group(1),
            constants,
        )

    decoded = _decode_string(value)

    if decoded is not None:
        return decoded

    identifier = value.removeprefix("global::").removeprefix("@").strip()

    if identifier in constants:
        return constants[identifier]

    if re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+",
        identifier,
    ):
        return identifier

    return ""


def method_source(
    source: str,
    method: CSharpMethodDeclaration,
) -> str:
    """Return the source range covered by a parsed method declaration."""
    lines = source.splitlines()
    start = max(method.line - 1, 0)
    end = min(
        max(method.line_end, method.line),
        len(lines),
    )
    return "\n".join(lines[start:end])


def statement_tail(
    source: str,
    end: int,
) -> str:
    """Return the remainder of the current statement after a call."""
    stop = source.find(";", end)

    if stop < 0:
        stop = min(
            len(source),
            end + 300,
        )

    return source[end:stop]


def first_argument(
    call: CSharpCall,
    names: Iterable[str],
    constants: dict[str, str],
    position: int | None = 0,
) -> str:
    """Resolve the first named argument or optional positional argument."""
    for name in names:
        if name in call.named_arguments:
            resolved = resolve_expression(
                call.named_arguments[name],
                constants,
            )

            if resolved:
                return resolved

    if position is not None and len(call.positional_arguments) > position:
        return resolve_expression(
            call.positional_arguments[position],
            constants,
        )

    return ""


def _split_callee(callee: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0

    for index, char in enumerate(callee):
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(depth - 1, 0)
        elif char == "." and depth == 0:
            parts.append(callee[start:index])
            start = index + 1

    parts.append(callee[start:])
    return [part for part in parts if part]


def _split_generic(
    value: str,
) -> tuple[str, tuple[str, ...]]:
    match = re.fullmatch(
        r"(?P<name>@?[A-Za-z_]\w*)"
        r"\s*<(?P<args>.+)>",
        value,
    )

    if not match:
        return value, ()

    return (
        match.group("name"),
        tuple(item.strip() for item in split_top_level(match.group("args")) if item.strip()),
    )


def _matching(
    source: str,
    start: int,
    opening: str,
    closing: str,
) -> int | None:
    depth = 0

    for index in range(start, len(source)):
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1

            if depth == 0:
                return index

    return None


def _assignment_target(
    source: str,
    position: int,
) -> str | None:
    boundary = max(
        source.rfind(";", 0, position),
        source.rfind("\n", 0, position),
    )
    prefix = source[boundary + 1 : position]
    match = re.search(
        rf"(?P<name>{_IDENTIFIER})"
        r"\s*=\s*(?:new\s+)?$",
        prefix,
    )

    return match.group("name").removeprefix("@") if match else None


def _named_argument(
    value: str,
) -> tuple[str | None, str]:
    depth = 0

    for index, char in enumerate(value):
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(depth - 1, 0)
        elif char == ":" and depth == 0:
            name = value[:index].strip()

            if re.fullmatch(_IDENTIFIER, name):
                return (
                    name.removeprefix("@"),
                    value[index + 1 :].strip(),
                )

    return None, value


def _decode_string(value: str) -> str | None:
    raw_match = re.fullmatch(
        r'\$*(?:@)?"""(.*)"""',
        value,
        re.DOTALL,
    )

    if raw_match:
        return raw_match.group(1)

    prefix_match = re.fullmatch(
        r"(?P<prefix>\$@|@\$|\$|@)?"
        r'"(?P<body>.*)"',
        value,
        re.DOTALL,
    )

    if not prefix_match:
        return None

    prefix = prefix_match.group("prefix") or ""
    body = prefix_match.group("body")

    if "@" in prefix:
        return body.replace('""', '"')

    replacements = {
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
        r"\"": '"',
        r"\\": "\\",
    }

    return re.sub(
        r"\\(?:n|r|t|\"|\\)",
        lambda match: replacements.get(
            match.group(0),
            match.group(0),
        ),
        body,
    )


def _balanced_outer(value: str) -> bool:
    depth = 0

    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1

            if depth == 0 and index != len(value) - 1:
                return False

    return depth == 0


__all__ = [
    "CSharpCall",
    "find_calls",
    "first_argument",
    "line_number",
    "mask_non_code",
    "method_source",
    "parse_arguments",
    "resolve_expression",
    "split_top_level",
    "statement_tail",
    "string_constants",
]
