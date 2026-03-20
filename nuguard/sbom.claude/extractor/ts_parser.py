"""TypeScript/JavaScript source parser.

Pure regex-based parser (no tree-sitter required).  Parses .ts, .tsx, .js,
.jsx source into a structured TSParseResult for use by TypeScript adapters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TSImport:
    """A single import statement."""

    module: str  # e.g. "@langchain/langgraph"
    names: list[str]  # named imports
    default: str | None  # default import name


@dataclass
class TSCall:
    """A function or constructor call."""

    name: str  # e.g. "StateGraph" or "new StateGraph"
    args: list[str]  # positional arg text (raw)
    kwargs: dict[str, str]  # named props from object literal {key: value}
    line: int


@dataclass
class TSStringLiteral:
    """A string literal in the source."""

    value: str
    context: str  # surrounding variable name or property key
    line: int
    is_template: bool  # backtick template literal


@dataclass
class TSParseResult:
    """Parsed result from a single TypeScript/JavaScript file."""

    imports: list[TSImport] = field(default_factory=list)
    calls: list[TSCall] = field(default_factory=list)
    decorators: list[tuple[str, int]] = field(default_factory=list)
    string_literals: list[TSStringLiteral] = field(default_factory=list)
    has_module: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# import { A, B as C } from 'module'
_RE_NAMED_IMPORT = re.compile(
    r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]"
)

# import DefaultName from 'module'
_RE_DEFAULT_IMPORT = re.compile(
    r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]"
)

# import * as Ns from 'module'
_RE_NAMESPACE_IMPORT = re.compile(
    r"import\s+\*\s+as\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]"
)

# require('module')
_RE_REQUIRE = re.compile(r"require\s*\(\s*['\"]([^'\"]+)['\"]")

# new ClassName(...) or ClassName(...)
_RE_CALL = re.compile(r"\b(new\s+)?(\w+)\s*\(")

# @decorator
_RE_DECORATOR = re.compile(r"@(\w+)")

# Double-quoted string literals (basic, no escape handling)
_RE_DQUOTE = re.compile(r'"([^"\\]{20,})"')

# Single-quoted string literals
_RE_SQUOTE = re.compile(r"'([^'\\]{20,})'")

# Backtick template literals (multi-line support via DOTALL)
_RE_TEMPLATE = re.compile(r"`([^`]{20,})`", re.DOTALL)

# Object literal property: key: "value"  or  key: `value`
_RE_OBJ_PROP = re.compile(
    r"""(\w+)\s*:\s*(?:"([^"\\]*)"|'([^'\\]*)'|`([^`]*)`|(\w[\w.-]*))\s*[,}]"""
)

# Context: variable assignment or property key preceding a call
_RE_CONTEXT = re.compile(
    r"(?:const|let|var|(\w+)\s*:)\s*(\w+)\s*=\s*$"
)

# Prompt-like context keywords
_PROMPT_CONTEXT_KEYWORDS = frozenset(
    {"prompt", "instruction", "system", "template", "message"}
)

# Prompt-like content triggers
_PROMPT_CONTENT_TRIGGERS = [
    "you are",
    "your task",
    "as an ai",
    "return json",
    "output format",
    "system:",
    "instructions:",
    "you must",
    "you should",
    "your role",
]


class TSParser:
    """Parse TypeScript/JavaScript source into a TSParseResult."""

    def parse(self, source: str) -> TSParseResult:
        result = TSParseResult()
        lines = source.splitlines()

        # --- Imports ---
        # Namespace imports first (most specific)
        for m in _RE_NAMESPACE_IMPORT.finditer(source):
            ns_name, module = m.group(1), m.group(2)
            result.imports.append(TSImport(module=module, names=[], default=ns_name))
            result.has_module.add(module)

        # Named imports: import { A, B } from 'module'
        for m in _RE_NAMED_IMPORT.finditer(source):
            names_raw, module = m.group(1), m.group(2)
            names = [n.strip().split(" as ")[-1].strip() for n in names_raw.split(",") if n.strip()]
            result.imports.append(TSImport(module=module, names=names, default=None))
            result.has_module.add(module)

        # Default imports
        for m in _RE_DEFAULT_IMPORT.finditer(source):
            default_name, module = m.group(1), m.group(2)
            # Skip if it looks like a namespace import (already captured)
            if f"* as {default_name}" not in source:
                result.imports.append(TSImport(module=module, names=[], default=default_name))
                result.has_module.add(module)

        # require()
        for m in _RE_REQUIRE.finditer(source):
            module = m.group(1)
            result.imports.append(TSImport(module=module, names=[], default=None))
            result.has_module.add(module)

        # --- Calls ---
        for m in _RE_CALL.finditer(source):
            is_new = bool(m.group(1))
            fn_name = m.group(2)
            if not fn_name or fn_name in _TS_KEYWORDS:
                continue
            call_start = m.end()
            # Find matching closing paren (simple depth tracking)
            args_text = self._extract_balanced(source, call_start - 1)
            # Compute line number
            line_no = source[: m.start()].count("\n") + 1
            # Extract kwargs from object literal in args
            kwargs = self._extract_kwargs(args_text)
            args = [args_text.strip()] if args_text.strip() else []
            name = ("new " + fn_name) if is_new else fn_name
            result.calls.append(
                TSCall(
                    name=name,
                    args=args,
                    kwargs=kwargs,
                    line=line_no,
                )
            )

        # --- Decorators ---
        for m in _RE_DECORATOR.finditer(source):
            dec_name = m.group(1)
            line_no = source[: m.start()].count("\n") + 1
            result.decorators.append((dec_name, line_no))

        # --- String literals ---
        # Double-quoted
        for m in _RE_DQUOTE.finditer(source):
            val = m.group(1)
            line_no = source[: m.start()].count("\n") + 1
            ctx = self._get_context(source, m.start(), lines)
            if self._is_likely_prompt(val, ctx) or len(val) >= 40:
                result.string_literals.append(
                    TSStringLiteral(value=val, context=ctx, line=line_no, is_template=False)
                )

        # Single-quoted
        for m in _RE_SQUOTE.finditer(source):
            val = m.group(1)
            line_no = source[: m.start()].count("\n") + 1
            ctx = self._get_context(source, m.start(), lines)
            if self._is_likely_prompt(val, ctx) or len(val) >= 40:
                result.string_literals.append(
                    TSStringLiteral(value=val, context=ctx, line=line_no, is_template=False)
                )

        # Template literals
        for m in _RE_TEMPLATE.finditer(source):
            val = m.group(1)
            line_no = source[: m.start()].count("\n") + 1
            ctx = self._get_context(source, m.start(), lines)
            if self._is_likely_prompt(val, ctx) or len(val) >= 40:
                result.string_literals.append(
                    TSStringLiteral(value=val, context=ctx, line=line_no, is_template=True)
                )

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_balanced(source: str, open_pos: int) -> str:
        """Extract the content inside balanced parentheses starting at open_pos."""
        depth = 0
        i = open_pos
        start = -1
        while i < len(source):
            ch = source[i]
            if ch == "(":
                depth += 1
                if depth == 1:
                    start = i + 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return source[start:i]
            i += 1
        return ""

    @staticmethod
    def _extract_kwargs(args_text: str) -> dict[str, str]:
        """Extract key-value pairs from an object literal in args."""
        kwargs: dict[str, str] = {}
        # Look for { ... } block
        brace_start = args_text.find("{")
        if brace_start == -1:
            return kwargs
        # Find matching }
        depth = 0
        i = brace_start
        obj_end = len(args_text)
        while i < len(args_text):
            if args_text[i] == "{":
                depth += 1
            elif args_text[i] == "}":
                depth -= 1
                if depth == 0:
                    obj_end = i + 1
                    break
            i += 1
        obj_text = args_text[brace_start:obj_end]
        for m in _RE_OBJ_PROP.finditer(obj_text):
            key = m.group(1)
            # Value: first non-None capture group among 2, 3, 4, 5
            val = m.group(2) or m.group(3) or m.group(4) or m.group(5) or ""
            kwargs[key] = val
        return kwargs

    @staticmethod
    def _get_context(source: str, pos: int, lines: list[str]) -> str:
        """Get the surrounding variable name or property key."""
        line_no = source[:pos].count("\n")
        if line_no < len(lines):
            line = lines[line_no]
            # Look for: const name = or key:
            m = re.search(r"(?:const|let|var)\s+(\w+)\s*=", line)
            if m:
                return m.group(1)
            m2 = re.search(r"(\w+)\s*:", line)
            if m2:
                return m2.group(1)
        return ""

    @staticmethod
    def _is_likely_prompt(text: str, context: str) -> bool:
        """Return True when a string is likely a prompt."""
        if len(text) < 40:
            return False
        ctx_lower = context.lower()
        if any(kw in ctx_lower for kw in _PROMPT_CONTEXT_KEYWORDS):
            return True
        text_lower = text.lower()
        return any(trigger in text_lower for trigger in _PROMPT_CONTENT_TRIGGERS)


# TypeScript/JS reserved words to skip when parsing calls
_TS_KEYWORDS = frozenset(
    {
        "if", "else", "for", "while", "do", "switch", "case",
        "return", "typeof", "instanceof", "void", "delete",
        "throw", "catch", "try", "finally", "new", "import",
        "export", "class", "extends", "super", "this", "async",
        "await", "yield", "in", "of", "let", "const", "var",
        "function", "debugger", "with", "break", "continue",
        "default", "interface", "type", "enum", "namespace",
        "module", "declare", "abstract", "implements", "from",
        "as", "null", "undefined", "true", "false",
        # Common but not class-like
        "console", "process", "require", "exports",
    }
)
