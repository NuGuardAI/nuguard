"""Prompt & PromptTemplate Detection TypeScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).  The tree-sitter path provides accurate
context (enclosing variable name, object property key) that dramatically
reduces false positives on JSDoc comments and non-prompt strings.

Supports:
- LangChain.js PromptTemplate, ChatPromptTemplate
- Vercel AI SDK generateText/streamText system prompts
- Template literal strings that look like prompts
- Injection risk scoring based on variable sources
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, TSStringLiteral, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


_PROMPT_PACKAGES = [
    "@langchain/core/prompts",
    "@langchain/prompts",
    "langchain/prompts",
    "ai",
    "@ai-sdk/core",
    "llamaindex",
]

_PROMPT_CLASSES = {
    "PromptTemplate": "langchain",
    "ChatPromptTemplate": "langchain",
    "SystemMessagePromptTemplate": "langchain",
    "HumanMessagePromptTemplate": "langchain",
    "FewShotPromptTemplate": "langchain",
}

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

_TEMPLATE_VAR_RES = [
    re.compile(r"\$\{([a-zA-Z_]\w*)\}"),
    re.compile(r"\{([a-zA-Z_]\w*)\}"),
    re.compile(r"\{\{([a-zA-Z_]\w*)\}\}"),
]

_JSDOC_MARKERS = [
    "@param",
    "@returns",
    "@return",
    "@throws",
    "@example",
    "@deprecated",
    "@type",
    "@typedef",
    "@property",
]

_HIGH_RISK_RE = re.compile(
    r"\b(req\.|request\.|params\.|query\.|body\.|formData|userInput|userMessage"
    r"|searchParams|cookies\.)\b"
)


def _is_likely_prompt(lit: TSStringLiteral) -> bool:
    text = lit.value
    if not text or len(text) < 40:
        return False
    text_lower = text.lower()
    ctx_lower = (lit.context or "").lower()

    # Skip if context strongly suggests non-prompt
    if ctx_lower in {"url", "path", "filename", "filepath", "html", "css", "sql", "query"}:
        return False

    # JSDoc block — only count as prompt if it has strong role/system cues
    jsdoc_count = sum(1 for m in _JSDOC_MARKERS if m in text_lower)
    if jsdoc_count >= 1:
        strong = [
            "you are",
            "your task is",
            "as an ai",
            "{context}",
            "{question}",
            "system:",
            "user:",
        ]
        if not any(s in text_lower for s in strong):
            return False

    # Context name is a strong prompt signal
    prompt_ctx_words = {"prompt", "instruction", "system", "template", "message", "persona"}
    if any(w in ctx_lower for w in prompt_ctx_words):
        if len(text) > 30:
            return True

    kw_count = sum(1 for kw in _PROMPT_KEYWORDS if kw in text_lower)
    if kw_count >= 2:
        return True

    if re.search(r"\byou are\s+(?:an?|the|a\s+helpful|assistant|expert)\b", text_lower):
        return True
    if "your role" in text_lower or "your task" in text_lower:
        return True

    for markers in _ROLE_MARKERS.values():
        if any(m.lower() in text_lower for m in markers):
            return True

    has_vars = any(r.search(text) for r in _TEMPLATE_VAR_RES)
    has_prompt_ctx = any(h in ctx_lower for h in ["prompt", "instruction", "system", "template"])
    if len(text) > 100 and has_vars and has_prompt_ctx and jsdoc_count == 0:
        return True

    return False


def _extract_vars(content: str) -> list[str]:
    vars_: list[str] = []
    for r in _TEMPLATE_VAR_RES:
        for m in r.finditer(content):
            v = m.group(1)
            if v not in vars_:
                vars_.append(v)
    return vars_


def _injection_risk(content: str, variables: list[str], source: str) -> float:
    if not variables:
        return 0.0
    risk = 0.3
    if _HIGH_RISK_RE.search(source):
        risk += 0.2
    risky_vars = {"userInput", "userMessage", "query", "prompt", "message", "input"}
    if any(any(rv.lower() in v.lower() for rv in risky_vars) for v in variables):
        risk += 0.15
    return min(risk, 1.0)


def _detect_role(content: str) -> str | None:
    cl = content.lower()[:500]
    for role, markers in _ROLE_MARKERS.items():
        if any(m.lower() in cl for m in markers):
            return role
    if any(p in cl for p in ["you are", "as an ai", "your role"]):
        return "system"
    return None


def _prompt_name(lit: TSStringLiteral, line: int) -> str:
    ctx = lit.context or lit.enclosing_function or ""
    if ctx:
        # Split camelCase/PascalCase into words before lowercasing
        ctx_split = re.sub(r"([a-z])([A-Z])", r"\1_\2", ctx)
        slug = re.sub(r"[^a-z0-9_]", "_", ctx_split.lower()).strip("_")
        if slug and slug not in {"prompt", "template", "message", "content", "text", "str"}:
            return slug.replace("_", " ").title()
    cl = lit.value.lower()[:400]
    if re.search(r"\byou are\s", cl):
        return "System Prompt"
    if any(k in cl for k in ["answer the question", "given the context"]):
        return "RAG Prompt"
    if any(k in cl for k in ["example:", "input:", "output:"]):
        return "Few Shot Prompt"
    if "summarize" in cl:
        return "Summarize Prompt"
    if "translate" in cl:
        return "Translate Prompt"
    return f"Prompt {line}"


class PromptTSAdapter(TSFrameworkAdapter):
    """Detect prompts and prompt templates in TypeScript/JavaScript files."""

    name = "prompt_ts"
    priority = 40
    handles_imports = _PROMPT_PACKAGES

    def can_handle(self, imports_present: set[str]) -> bool:
        # Always run — prompts can appear in any file; _detect will filter
        return True

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result: TSParseResult = (
            parse_result
            if isinstance(parse_result, TSParseResult)
            else parse_typescript(content, file_path)
        )

        detected: list[ComponentDetection] = []
        source = result.source or content

        # --- PromptTemplate class instantiations ---
        for inst in result.instantiations:
            ns = _PROMPT_CLASSES.get(inst.class_name)
            if ns is None:
                continue
            template = self._resolve(inst, "template", "0") or ""
            all_vars = _extract_vars(template)
            risk = _injection_risk(template, all_vars, source)
            name = (
                _prompt_name(
                    TSStringLiteral(value=template, line_number=inst.line_start),
                    inst.line_start,
                )
                if template
                else inst.class_name
            )
            canon = canonicalize_text(name.lower())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": ns,
                        "role": "system",
                        "is_template": True,
                        "template_class": inst.class_name,
                        "template_variables": all_vars,
                        "injection_risk_score": risk,
                        "content": template,
                        "char_count": len(template),
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                )
            )

        # --- Prompt-like string literals ---
        # tree-sitter provides accurate context (variable name, property key, function name)
        # which the _is_likely_prompt heuristic uses to cut false positives
        for lit in result.string_literals:
            if not lit.is_potential_prompt:
                continue
            if not _is_likely_prompt(lit):
                continue
            template_vars = _extract_vars(lit.value)
            risk = _injection_risk(lit.value, template_vars, source) if template_vars else 0.0
            name = _prompt_name(lit, lit.line_number)
            canon = canonicalize_text(name.lower())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.75 if template_vars else 0.65,
                    metadata={
                        "is_template": len(template_vars) > 0,
                        "is_template_literal": lit.is_template,
                        "template_variables": template_vars,
                        "injection_risk_score": risk,
                        "role": _detect_role(lit.value),
                        "context": lit.context,
                        "enclosing_function": lit.enclosing_function,
                        "content": lit.value,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=lit.line_number,
                    snippet=lit.value[:80],
                    evidence_kind="ast_string_literal",
                )
            )

        return detected


PROMPT_TS_IMPORTS = _PROMPT_PACKAGES
PROMPT_TS_CLASSES = list(_PROMPT_CLASSES.keys())
