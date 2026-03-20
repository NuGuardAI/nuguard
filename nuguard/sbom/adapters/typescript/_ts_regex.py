"""TSFrameworkAdapter base class for TypeScript/JavaScript adapters.

Parsing is handled by ``xelo.core.ts_parser.TypeScriptParser``, which
uses tree-sitter when available and falls back to regex otherwise.

This module only defines the shared base class so adapters do not need to
import from extractor internals.
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter
from xelo.types import ComponentType


class TSFrameworkAdapter(FrameworkAdapter):
    """Base class for TypeScript/JavaScript FrameworkAdapters.

    Differences from ``FrameworkAdapter``:

    * ``can_handle()`` uses substring matching against ``handles_imports`` so
      scoped npm packages like ``@langchain/langgraph`` and ``@anthropic-ai/sdk``
      are detected correctly regardless of sub-path.
    * ``extract()`` receives a ``TSParseResult | None`` as *parse_result*.
      When *parse_result* is ``None``, implementations must call
      ``parse_typescript(content, file_path)`` from
      ``xelo.core.ts_parser`` themselves.
    """

    def can_handle(self, imports_present: set[str]) -> bool:
        for mod in imports_present:
            for pkg in self.handles_imports:
                if mod == pkg or pkg in mod:
                    return True
        return False

    # ------------------------------------------------------------------
    # Helpers shared across all TS adapters
    # ------------------------------------------------------------------

    def _detect(self, result: Any) -> bool:
        """Return True if any of this adapter's packages appear in the imports."""
        for imp in result.imports:
            for pkg in self.handles_imports:
                if pkg in imp.module or imp.module == pkg:
                    return True
        return False

    def _fw_node(self, file_path: str, line: int = 0) -> ComponentDetection:
        """Emit a FRAMEWORK presence node."""
        return ComponentDetection(
            component_type=ComponentType.FRAMEWORK,
            canonical_name=f"framework:{self.name}",
            display_name=f"framework:{self.name}",
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.95,
            metadata={"framework": self.name, "language": "typescript"},
            file_path=file_path,
            line=line,
            snippet=f"import {self.name}",
            evidence_kind="ast_import",
        )

    @staticmethod
    def _resolve(inst_or_call: Any, *keys: str) -> str:
        """Return the first non-empty resolved (or raw) value for any of *keys*.

        Prefers ``resolved_arguments`` (with symbol-table expansion) over the
        raw ``arguments`` dict, and strips surrounding quotes.
        """
        resolved = getattr(inst_or_call, "resolved_arguments", {}) or {}
        raw = getattr(inst_or_call, "arguments", {}) or {}
        for key in keys:
            val = resolved.get(key) or raw.get(key)
            if val:
                return TSFrameworkAdapter._clean(val)
        return ""

    @staticmethod
    def _clean(value: Any) -> str:
        """Strip surrounding quotes/backticks and reject placeholder tokens."""
        if value is None:
            return ""
        s = str(value).strip("'\"` ")
        if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
            return ""
        return s

    @staticmethod
    def _assignment_name(source: str, line: int) -> str | None:
        """Try to extract the LHS variable name from an assignment on *line*."""
        if not source:
            return None
        lines = source.splitlines()
        if line < 1 or line > len(lines):
            return None
        m = re.search(
            r"^\s*(?:export\s+)?(?:const|let|var)?\s*"
            r"([A-Za-z_]\w*)\s*(?::\s*\w[\w<>, ]*?)?\s*=",
            lines[line - 1],
        )
        return m.group(1) if m else None

    @staticmethod
    def _template_vars(text: str) -> list[str]:
        """Extract unique template variable names from {var}, ${var}, and {{var}} patterns."""
        vars_: list[str] = []
        seen: set[str] = set()
        for pat in (
            r"\$\{([a-zA-Z_]\w*)\}",  # ${var} — JS template literals
            r"\{\{([a-zA-Z_]\w*)\}\}",  # {{var}} — Handlebars / Jinja2
            r"(?<!\$)\{([a-zA-Z_]\w*)\}",  # {var}  — Python-style templates
        ):
            for m in re.finditer(pat, text):
                v = m.group(1)
                if v not in seen:
                    seen.add(v)
                    vars_.append(v)
        return vars_
