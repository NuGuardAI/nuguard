"""C# prompt-constant extractor — detects system prompts and prompt templates.

Covers:
- ``const string`` / ``static readonly string`` assignments containing
  prompt-like text (role markers, instruction phrases, template variables)
- Interpolated prompt strings with ``{variable}`` expressions
- Long string literals in field/property assignments that match prompt heuristics

Detection is content-driven: the adapter always runs (``can_handle`` returns
True) and filters ``CSharpStringLiteral`` entries from the parse result using
prompt-specific heuristics inspired by the TypeScript ``PromptTSAdapter``.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._csharp_base import CSharpFrameworkAdapter
from ._source import mask_non_code

# ---------------------------------------------------------------------------
# Prompt detection heuristics
# ---------------------------------------------------------------------------

_PROMPT_KEYWORDS = [
    "you are",
    "your task",
    "as an ai",
    "as a helpful",
    "given the following",
    "answer the question",
    "respond in",
    "return json",
    "output format",
    "few-shot",
    "examples:",
    "system:",
    "user:",
    "assistant:",
    "human:",
    "instructions:",
    "context:",
    "question:",
    "summarize",
    "translate",
]

_ROLE_MARKERS = {
    "system": ["system:", "system message", "<|system|>", "[system]", "### system"],
    "user": ["user:", "human:", "<|user|>", "[user]", "### user"],
    "assistant": ["assistant:", "<|assistant|>", "[assistant]", "ai:"],
}

_TEMPLATE_VAR_RE = [
    re.compile(r"\{([a-zA-Z_]\w*)\}"),
    re.compile(r"\{\{([a-zA-Z_]\w*)\}\}"),
]

_HIGH_RISK_RE = re.compile(
    r"\b(request\.|params\.|query\.|body\.|userInput|userMessage"
    r"|searchParams|input)\b"
)

# Matches ``const string <name> =`` or ``static readonly string <name> =``
_CONST_READONLY_RE = re.compile(
    r"\b(?:const|static\s+readonly)\s+(?:string|var)\s+"
    r"(?P<name>@?[A-Za-z_]\w*)\s*=",
)

# Prompt-context variable names that strongly suggest a prompt assignment
_PROMPT_FIELD_NAMES = {
    "prompt",
    "systemprompt",
    "system_prompt",
    "instruction",
    "instructions",
    "systemmessage",
    "system_message",
    "template",
    "persona",
    "systeminstructions",
    "system_instructions",
    "sysprompt",
    "sys_prompt",
    "llmprompt",
    "llm_prompt",
    "ai_prompt",
    "aiprompt",
}


def _is_prompt_constant(
    value: str,
    assigned_to: str | None,
    line_source: str,
) -> bool:
    """Return True if the string looks like an AI prompt constant."""
    if not value or len(value) < 30:
        return False

    lowered = value.lower()
    name_lower = (assigned_to or "").lower()

    # Strong signal: variable name contains prompt-related words
    if any(w in name_lower for w in _PROMPT_FIELD_NAMES):
        if len(value) > 20:
            return True

    # Strong signal: const/readonly on the source line
    if line_source and _CONST_READONLY_RE.search(line_source):
        kw_count = sum(1 for kw in _PROMPT_KEYWORDS if kw in lowered)
        if kw_count >= 2:
            return True
        if kw_count >= 1 and len(value) > 80:
            return True
        # Role marker at line start
        for markers in _ROLE_MARKERS.values():
            if any(lowered.lstrip().startswith(m.lower()) for m in markers):
                return True

    # Non-const but still prompt-like: long string with strong cues
    if len(value) > 100:
        kw_count = sum(1 for kw in _PROMPT_KEYWORDS if kw in lowered)
        if kw_count >= 2:
            return True
        for markers in _ROLE_MARKERS.values():
            if any(m.lower() in lowered for m in markers):
                return True

    return False


def _extract_vars(text: str) -> list[str]:
    """Extract unique variable names from interpolation braces."""
    variables: list[str] = []
    seen: set[str] = set()
    for regex in _TEMPLATE_VAR_RE:
        for m in regex.finditer(text):
            v = m.group(1)
            if v not in seen:
                seen.add(v)
                variables.append(v)
    return variables


def _injection_risk(
    content: str,
    variables: list[str],
    source: str,
) -> float:
    """Score injection risk 0.0–1.0 based on variable sources."""
    if not variables:
        return 0.0
    risk = 0.3
    if _HIGH_RISK_RE.search(source):
        risk += 0.2
    risky = {"userInput", "userMessage", "query", "prompt", "message", "input"}
    if any(any(rv.lower() in v.lower() for rv in risky) for v in variables):
        risk += 0.15
    return min(risk, 1.0)


def _detect_role(content: str) -> str | None:
    """Detect the prompt role from content markers."""
    cl = content.lower()[:500]
    for role, markers in _ROLE_MARKERS.items():
        if any(m.lower() in cl for m in markers):
            return role
    if any(p in cl for p in ["you are", "as an ai", "your role"]):
        return "system"
    return None


def _prompt_name(assigned_to: str | None, content: str, line: int) -> str:
    """Derive a human-readable name for the prompt."""
    if assigned_to:
        name = assigned_to.strip("_").replace("_", " ").title()
        if name.lower() not in {"prompt", "template", "message", "content", "text", "string"}:
            return name
    cl = content.lower()[:400]
    if "you are" in cl:
        return "System Prompt"
    if any(k in cl for k in ["answer the question", "given the context"]):
        return "RAG Prompt"
    if any(k in cl for k in ["example:", "input:", "output:"]):
        return "Few Shot Prompt"
    if "summarize" in cl:
        return "Summarize Prompt"
    if "translate" in cl:
        return "Translate Prompt"
    return f"Prompt L{line}"


class CSharpPromptAdapter(CSharpFrameworkAdapter):
    """Detect prompt constants and prompt-like string literals in C# files."""

    name = "csharp_prompts"
    priority = 40  # After framework adapters, before generic regex

    def can_handle(self, imports_present: set[str]) -> bool:  # noqa: ARG002
        """Always run — prompts can appear in any C# file."""
        return True

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(content, file_path, parse_result)
        code = mask_non_code(content)

        detected: list[ComponentDetection] = []

        for lit in result.string_literals:
            if not lit.is_potential_prompt:
                continue

            # Get the source line for const/readonly detection
            lines = code.splitlines()
            line_src = lines[lit.line - 1] if 0 < lit.line <= len(lines) else ""

            if not _is_prompt_constant(lit.value, lit.assigned_to, line_src):
                continue

            template_vars = _extract_vars(lit.value)
            risk = _injection_risk(lit.value, template_vars, code)
            name = _prompt_name(lit.assigned_to, lit.value, lit.line)
            canon = canonicalize_text(name.lower())

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88 if template_vars else 0.82,
                    metadata={
                        "framework": "csharp_prompts",
                        "role": _detect_role(lit.value),
                        "is_template": len(template_vars) > 0,
                        "is_interpolated": lit.is_interpolated,
                        "template_variables": template_vars,
                        "injection_risk_score": risk,
                        "assigned_to": lit.assigned_to,
                        "enclosing_method": lit.enclosing_method,
                        "content": lit.value[:500],
                        "char_count": len(lit.value),
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=lit.line,
                    snippet=lit.value[:80],
                    evidence_kind="ast_string_literal",
                )
            )

        return detected


__all__ = ["CSharpPromptAdapter"]
