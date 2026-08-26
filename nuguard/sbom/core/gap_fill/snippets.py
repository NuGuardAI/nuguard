"""File selection and snippet-building for the LLM gap-fill pass.

Also hosts small text-context helpers (`extract_context`, `detect_language`)
shared with ``nuguard.sbom.core.verification`` — both modules need to turn a
(file content, line number) pair into a bounded code window for an LLM
prompt, so this is the single place that logic lives.
"""

from __future__ import annotations

from pathlib import Path

from ...types import ComponentType
from .categories import (
    _CATEGORY_KEYWORDS,
    _GAP_FILL_SKIP_EXTENSIONS,
    _GAP_FILL_SKIP_PATH_PARTS,
    _GAP_FILL_SKIP_STEMS,
    _MAX_LINES_PER_FILE,
    _MAX_SNIPPET_CHARS,
)


def is_gap_fill_source_file(path: str) -> bool:
    """Return True only if *path* is a code file suitable for gap-fill context.

    Excludes documentation (.md, *.rst …), test directories, example
    directories, and deployment-guide files whose descriptive prose consistently
    causes the LLM to hallucinate components that are only *mentioned*, not
    *used*, in the codebase.
    """
    p = Path(path)
    stem = p.stem.lower()
    suffix = p.suffix.lower()
    parts = {part.lower() for part in p.parts}

    if suffix in _GAP_FILL_SKIP_EXTENSIONS:
        return False
    if stem in _GAP_FILL_SKIP_STEMS:
        return False
    if "readme" in stem or "deploy" in stem or "install" in stem:
        return False
    if parts & _GAP_FILL_SKIP_PATH_PARTS:
        return False
    return True


def score_file_for_category(content: str, keywords: list[str], rel_path: str) -> int:
    """Return a keyword-hit score for *content* (used to rank files)."""
    text_lower = content.lower()
    score = sum(text_lower.count(kw) for kw in keywords)
    path_lower = rel_path.lower()
    score += sum(3 for kw in keywords if kw in path_lower)
    return score


def build_file_snippets(
    category: ComponentType,
    file_contents: dict[str, str],
    *,
    extra_paths: set[str] | None = None,
) -> str:
    """Return a single concatenated snippet string for the LLM prompt.

    *extra_paths*, when given, restricts scoring to only those paths (used by
    the PROMPT probe signal to target files that are normally excluded from
    gap-fill context — see gating.py).
    """
    keywords = _CATEGORY_KEYWORDS.get(category, [])
    if not keywords:
        return ""

    scored: list[tuple[int, str, str]] = []
    for path, content in file_contents.items():
        if extra_paths is not None and path not in extra_paths:
            continue
        if extra_paths is None and not is_gap_fill_source_file(path):
            continue
        score = score_file_for_category(content, keywords, path)
        if score > 0:
            scored.append((score, path, content))

    scored.sort(key=lambda t: t[0], reverse=True)

    parts: list[str] = []
    total_chars = 0
    for _, path, content in scored:
        if total_chars >= _MAX_SNIPPET_CHARS:
            break
        lines = content.splitlines()[:_MAX_LINES_PER_FILE]
        snippet_text = "\n".join(lines)
        remaining = _MAX_SNIPPET_CHARS - total_chars
        if len(snippet_text) > remaining:
            snippet_text = snippet_text[:remaining] + "\n...(truncated)"
        parts.append(f"### {path}\n{snippet_text}")
        total_chars += len(snippet_text)

    return "\n\n".join(parts)


def detect_language(file_path: str) -> str:
    """Best-effort language tag for a file path, used in code-fenced prompts."""
    if not file_path:
        return "python"
    lower = file_path.lower()
    if lower.endswith((".ts", ".tsx")):
        return "typescript"
    if lower.endswith((".js", ".jsx", ".mjs")):
        return "javascript"
    if lower.endswith((".yaml", ".yml")):
        return "yaml"
    if lower.endswith(".tf"):
        return "hcl"
    if lower.endswith(".json"):
        return "json"
    return "python"


def extract_context(file_content: str, line_number: int, context_lines: int = 20) -> str:
    """Return a window of *context_lines* around *line_number* in *file_content*."""
    if not file_content or line_number <= 0:
        return "(context not available)"
    lines = file_content.splitlines()
    start = max(0, line_number - context_lines // 2 - 1)
    end = min(len(lines), line_number + context_lines // 2)
    return "\n".join(lines[start:end])
