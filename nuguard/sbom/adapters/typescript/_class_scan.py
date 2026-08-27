"""Shared TypeScript class/constructor scanning primitives.

Several NestJS-flavored TS adapters (``nestjs_adapter.py``, ``nestjs_tool_di.py``,
``auth_detector.py``, ``agent_orchestrator.py``) all scan class bodies line-by-line
via regex/brace-counting rather than a real AST (there is no shared TS class/method
AST base in this package). This module holds the primitives they all need, so none
of them has to import internals from an unrelated sibling adapter.
"""

from __future__ import annotations

import re

_CLASS_RE = re.compile(r"\bclass\s+(\w+)")

_CONSTRUCTOR_START_RE = re.compile(r"\bconstructor\s*\(")

# Parses one constructor parameter, e.g. "private readonly aiService: AiService"
# or "@Inject(TOKEN) private readonly foo: FooService". Type may carry generics
# / union members after it; we only need the leading type identifier.
_PARAM_RE = re.compile(
    r"(?:@\w+\([^)]*\)\s*)?"  # optional parameter decorator, e.g. @Inject(...)
    r"(?:public|private|protected)?\s*(?:readonly\s+)?"
    r"\w+\s*:\s*([A-Za-z_]\w*)"
)


def _find_class_body_span(lines: list[str], class_line_idx: int) -> tuple[int, int]:
    """Return ``(body_start, body_end)`` line indices (inclusive) for the class
    (or method) whose declaration is on ``class_line_idx``, via brace counting."""
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


def _parse_constructor_params(
    lines: list[str], body_start: int, body_end: int
) -> list[tuple[int, str]] | None:
    """Return ``(line_idx, param_text)`` pairs for the constructor's parameter
    list, or None if not found.

    Parameters keep their own source line (real-world NestJS constructors are
    conventionally formatted one parameter per line — see studyield-app's
    ``research.service.ts``/``chat.service.ts``) rather than all being
    attributed to the constructor's opening line. Collapsing them onto one
    shared line previously made ``core.py``'s ``_dedup_by_location`` pass
    treat two genuinely distinct injected services as duplicate detections
    of "the same source token" and silently drop one at random (tie-broken
    by an unstable set-iteration order across process runs).
    """
    for i in range(body_start, body_end + 1):
        m = _CONSTRUCTOR_START_RE.search(lines[i])
        if not m:
            continue
        depth = lines[i].count("(") - lines[i].count(")")
        collected = [(i, lines[i][m.end():])]
        j = i
        while depth > 0 and j < body_end:
            j += 1
            depth += lines[j].count("(") - lines[j].count(")")
            collected.append((j, lines[j]))
        # Trim the trailing ")" (and anything after, e.g. "{ ... }") on the
        # last collected line, which closed the parameter list.
        last_idx, last_text = collected[-1]
        close_idx = last_text.rfind(")")
        if close_idx != -1:
            collected[-1] = (last_idx, last_text[:close_idx])

        params: list[tuple[int, str]] = []
        for line_idx, text in collected:
            for chunk in text.split(","):
                chunk = chunk.strip()
                if chunk:
                    params.append((line_idx, chunk))
        return params
    return None


def _line_index_at(content: str, offset: int) -> int:
    """0-indexed line number for a character offset into *content*."""
    return content.count("\n", 0, offset)
