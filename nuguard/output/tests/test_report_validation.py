"""Tests for the structural Markdown checker used on generated reports."""
from __future__ import annotations

from nuguard.output.report_validation import validate_markdown_report


def test_empty_report_is_reported() -> None:
    assert validate_markdown_report("") == ["Report is empty"]


def test_clean_report_has_no_issues() -> None:
    text = "\n".join(
        [
            "# Report",
            "",
            "| Name | Value |",
            "|---|---|",
            "| a | b |",
            "",
            "```",
            "code",
            "```",
            "",
            "See [the docs](https://example.com/docs).",
        ]
    )
    assert validate_markdown_report(text) == []


def test_unbalanced_code_fence_is_reported() -> None:
    assert validate_markdown_report("# R\n\n```\nnever closed\n") == [
        "Unbalanced fenced code blocks"
    ]


def test_malformed_table_row_reports_its_line_number() -> None:
    # A row with fewer than three pipes has fewer than two cells' worth of structure.
    issues = validate_markdown_report("# R\n|x|\n")
    assert issues == ["Line 2: malformed table row"]


def test_well_formed_table_row_is_accepted() -> None:
    assert validate_markdown_report("| a | b |\n") == []


def test_unclosed_link_target_is_reported() -> None:
    issues = validate_markdown_report("Read [the guide](https://example.com/guide\n")
    assert issues == ["Potentially broken markdown link syntax"]


def test_closed_link_is_accepted() -> None:
    assert validate_markdown_report("Read [the guide](https://example.com/guide)\n") == []


def test_ansi_escape_sequences_are_reported() -> None:
    issues = validate_markdown_report("# R\n\x1b[31mred\x1b[0m\n")
    assert issues == ["ANSI escape sequences detected in output"]


def test_multiple_problems_are_all_reported() -> None:
    issues = validate_markdown_report("```\n|x|\n\x1b[0m\n")
    assert "Unbalanced fenced code blocks" in issues
    assert "Line 2: malformed table row" in issues
    assert "ANSI escape sequences detected in output" in issues
