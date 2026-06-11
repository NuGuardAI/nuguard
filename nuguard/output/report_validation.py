"""Markdown report validation helpers for CLI output safeguards."""
from __future__ import annotations

import re


def validate_markdown_report(text: str) -> list[str]:
    """Return a list of structural markdown issues detected in a report."""
    issues: list[str] = []
    if not text:
        return ["Report is empty"]

    fence_count = text.count("```")
    if fence_count % 2 != 0:
        issues.append("Unbalanced fenced code blocks")

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        if line.startswith("|") and line.endswith("|"):
            cols = line.count("|")
            if cols < 3:
                issues.append(f"Line {idx}: malformed table row")

    if re.search(r"\[[^\]]*\]\([^\)]*$", text, flags=re.MULTILINE):
        issues.append("Potentially broken markdown link syntax")

    if "\x1b[" in text:
        issues.append("ANSI escape sequences detected in output")

    return issues
