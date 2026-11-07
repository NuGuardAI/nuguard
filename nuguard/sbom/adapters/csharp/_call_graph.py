"""Best-effort static call graph used to link ASP.NET endpoints to agent hosts.

A C# endpoint handler usually delegates to an injected service
(``_chatService.GetChatCompletionAsync(...)``), which delegates again until some
type builds the agents (``new ChatCompletionAgent { ... }``). This module indexes
which methods call which, by *declared type name* only: receivers are typed from
fields, properties, locals and parameters, and interfaces expand to the types that
list them as a base. Anything it cannot type is dropped, so an unresolved call
yields no link rather than a guessed one.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from ...core.csharp_parser import CSharpParseResult, parse_csharp
from ._source import find_calls, mask_non_code

# Chains longer than this are not followed (also bounds work on cyclic call graphs).
_MAX_DEPTH = 8
_NOT_TYPES = {
    "return",
    "await",
    "throw",
    "yield",
    "else",
    "new",
    "using",
    "goto",
    "case",
    "var",
    "out",
    "in",
    "ref",
    "namespace",
}
_AGENT_NEW_RE = re.compile(r"\bnew\s+ChatCompletionAgent\b|\bChatCompletionAgent\s+@?\w+\s*=\s*new\s*\(")
_DECL_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:private|public|internal|protected|readonly|static|required)\s+)*"
    r"(?P<type>[A-Za-z_][\w.]*(?:<[^;=(){}]*>)?\??)\s+(?P<name>@?[A-Za-z_]\w*)\s*"
    r"(?:;|=(?!>)|\{\s*(?:get|init|set))"
)
_VAR_NEW_RE = re.compile(
    r"\bvar\s+(?P<name>@?[A-Za-z_]\w*)\s*=\s*(?:await\s+)?new\s+(?P<type>[A-Za-z_][\w.]*)"
)
_PARAM_TYPE_RE = re.compile(r"[A-Za-z_][\w.<>,?\[\] ]*")
_PARAM_NAME_RE = re.compile(r"@?[A-Za-z_]\w*")
_MAX_PARAM_CHARS = 512

# Endpoint metadata key carrying the files of agent-building types the handler reaches.
AGENT_HOST_FILES_KEY = "agent_host_files"

CallGraph = dict[str, Any]


def empty_call_graph() -> CallGraph:
    return {"methods": {}, "agent_types": {}, "implementers": {}}


def _simple_type(name: str) -> str:
    name = re.sub(r"<.*>", "", name).replace("?", "").replace("[]", "").strip()
    return name.split(".")[-1]


def _parameter(value: str) -> tuple[str, str] | None:
    clean = re.sub(r"\[[^\]]*\]\s*", "", value).split("=", 1)[0].strip()
    clean = re.sub(r"\b(?:in|out|params|ref|scoped|this)\s+", "", clean)

    if len(clean) > _MAX_PARAM_CHARS:
        return None

    parts = clean.rsplit(None, 1)

    if len(parts) != 2:
        return None

    type_text, name = parts[0].strip(), parts[1]

    if not _PARAM_TYPE_RE.fullmatch(type_text) or not _PARAM_NAME_RE.fullmatch(name):
        return None

    return name.removeprefix("@"), _simple_type(type_text)


def _innermost(spans: list[Any], line: int) -> Any | None:
    best = None

    for span in spans:
        if span.line <= line <= max(span.line_end, span.line) and (
            best is None or span.line >= best.line
        ):
            best = span

    return best


def enclosing_type(result: CSharpParseResult, line: int) -> str:
    """Return the innermost type declared around *line*, or an empty string."""
    declaration = _innermost(result.type_declarations, line)
    return declaration.name if declaration is not None else ""


def member_types(content: str, result: CSharpParseResult) -> dict[str, dict[str, str]]:
    """Return ``type -> {member, local or parameter name: declared type}``."""
    masked = mask_non_code(content)
    members: dict[str, dict[str, str]] = {}

    def record(owner: str, name: str, type_name: str) -> None:
        simple = _simple_type(type_name)

        if simple and simple not in _NOT_TYPES:
            members.setdefault(owner, {}).setdefault(name.removeprefix("@"), simple)

    for pattern in (_DECL_RE, _VAR_NEW_RE):
        for match in pattern.finditer(masked):
            declaration = _innermost(result.type_declarations, masked.count("\n", 0, match.start()) + 1)

            if declaration is not None:
                record(declaration.name, match.group("name"), match.group("type"))

    for method in result.method_declarations:
        for value in method.parameters:
            parsed = _parameter(value)

            if parsed and method.containing_type:
                record(method.containing_type, parsed[0], parsed[1])

    return members


def _resolve(
    receiver: str | None,
    name: str,
    owner: str,
    scope: dict[str, str],
) -> tuple[str, str] | None:
    if receiver is None or receiver == "this":
        return owner, name

    root = receiver.removeprefix("this.").lstrip("@")

    if not re.fullmatch(r"[A-Za-z_]\w*", root):
        return None

    return scope.get(root, root), name


def collect_call_graph(content: str, file_path: str = "") -> CallGraph:
    """Return the call-graph fragment of one C# file."""
    graph = empty_call_graph()

    if "class" not in content and "interface" not in content:
        return graph

    result = parse_csharp(content, file_path)

    for declaration in result.type_declarations:
        for base in declaration.base_types:
            graph["implementers"].setdefault(_simple_type(base), []).append(declaration.name)

    masked = mask_non_code(content)

    for match in _AGENT_NEW_RE.finditer(masked):
        agent_owner = _innermost(result.type_declarations, masked.count("\n", 0, match.start()) + 1)

        if agent_owner is not None:
            graph["agent_types"][agent_owner.name] = file_path

    members = member_types(content, result)
    methods = [method for method in result.method_declarations if method.containing_type]

    if not methods:
        return graph

    for call in find_calls(content):
        if call.is_constructor:
            continue

        method = _innermost(methods, call.line)

        if method is None:
            continue

        owner = method.containing_type or ""
        target = _resolve(call.receiver, call.name, owner, members.get(owner, {}))

        if target is not None:
            graph["methods"].setdefault(f"{owner}.{method.name}", []).append(target)

    return graph


def merge_call_graph(into: CallGraph, fragment: CallGraph) -> None:
    """Fold one file's fragment into the repository-wide graph."""
    for key, callees in fragment["methods"].items():
        merged = into["methods"].setdefault(key, [])
        merged.extend(callee for callee in callees if callee not in merged)

    into["agent_types"].update(fragment["agent_types"])

    for base, types in fragment["implementers"].items():
        merged_types = into["implementers"].setdefault(base, [])
        merged_types.extend(item for item in types if item not in merged_types)


def handler_agent_files(
    graph: CallGraph,
    content: str,
    result: CSharpParseResult,
    handler_text: str,
    owner: str,
    parameters: list[str],
) -> list[str]:
    """Return files of agent-building types an endpoint handler reaches.

    *owner* is the type declaring the handler; *parameters* are the handler's own
    parameter declarations (a Minimal API lambda may take its service by injection).
    """
    if not graph["agent_types"]:
        return []

    scope = dict(member_types(content, result).get(owner, {}))

    for value in parameters:
        parsed = _parameter(value)

        if parsed:
            scope[parsed[0]] = parsed[1]

    seeds = [
        target
        for call in find_calls(handler_text)
        if not call.is_constructor
        and (target := _resolve(call.receiver, call.name, owner, scope)) is not None
    ]
    queue = deque((*seed, 0) for seed in seeds)
    seen: set[tuple[str, str]] = set()
    files: set[str] = set()

    while queue:
        type_name, name, depth = queue.popleft()

        for concrete in (type_name, *graph["implementers"].get(type_name, ())):
            if (concrete, name) in seen:
                continue

            seen.add((concrete, name))

            if concrete in graph["agent_types"]:
                files.add(graph["agent_types"][concrete])

            if depth < _MAX_DEPTH:
                queue.extend(
                    (*callee, depth + 1) for callee in graph["methods"].get(f"{concrete}.{name}", ())
                )

    return sorted(files)
