"""Go module-level prompt-constant extraction.

Mirrors ``extractor/core.py``'s ``_extract_python_prompt_constants`` for Go.
Go's idiom for a system prompt is a package-level ``const``/``var`` raw
string literal::

    const systemPrompt = `You are Mosaic's health assistant...`

Unlike Python's SCREAMING_SNAKE_CASE convention (``SYSTEM_PROMPT``), Go
exported/unexported names are distinguished by case, not underscores, and
prompt constants are typically camelCase or PascalCase (``systemPrompt``,
``SupplementSysPrompt``) — so the name match here is a case-insensitive
``...Prompt`` suffix rather than Python's ``(?:^|_)PROMPT$`` anchor.

Called unconditionally on every ``.go`` file's ``GoParseResult`` in
``extractor/core.py``, regardless of which (if any) framework adapters
matched, so prompt-only files are still covered.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._go_base import GoFrameworkAdapter

_PROMPT_NAME_RE = re.compile(r"prompt$", re.IGNORECASE)
# Keywords in the constant name that indicate an evaluation/testing artifact
# rather than a production AI prompt — mirrors the Python skip list.
_PROMPT_SKIP_WORDS = frozenset(
    {"EVAL", "EVALUATE", "EVALUATION", "GROUNDTRUTH", "GROUND_TRUTH", "TEST_PROMPT", "MOCK_PROMPT"}
)
_MIN_PROMPT_LENGTH = 80  # minimum char count to treat as a real prompt


def extract_go_prompt_constants(parse_result: Any, rel_path: str) -> list[ComponentDetection]:
    """Emit PROMPT nodes for package-level ``...Prompt`` string constants."""
    detections: list[ComponentDetection] = []

    for lit in parse_result.string_literals:
        # ``context`` is the enclosing function/method name, or None when the
        # literal sits at package scope — only package-level constants count.
        if lit.context is not None:
            continue

        name = lit.assigned_to or ""
        if not name or not _PROMPT_NAME_RE.search(name):
            continue
        if len(lit.value) < _MIN_PROMPT_LENGTH:
            continue
        if any(skip in name.upper() for skip in _PROMPT_SKIP_WORDS):
            continue

        canon = canonicalize_text(name.lower())
        template_vars = GoFrameworkAdapter._template_vars(lit.value)

        detections.append(
            ComponentDetection(
                component_type=ComponentType.PROMPT,
                canonical_name=canon,
                display_name=name,
                adapter_name="go_prompt_const",
                priority=50,
                confidence=0.80,
                metadata={
                    "role": "system" if "system" in name.lower() else "unspecified",
                    "content": lit.value,
                    "char_count": len(lit.value),
                    "is_template": bool(template_vars),
                    "template_variables": template_vars,
                    "language": "golang",
                },
                file_path=rel_path,
                line=lit.line,
                snippet=lit.value[:80] + ("..." if len(lit.value) > 80 else ""),
                evidence_kind="ast_constant",
            )
        )

    return detections


__all__ = ["extract_go_prompt_constants"]
