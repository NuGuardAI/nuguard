"""Tests for the shared humanize_context_name() helper (extracted from
duplicated logic in prompts.py and langgraph.py — docs/sbom-accuracy-plan.md #2).

Pins the mangled-whitespace bug: several adjacent non-alphanumeric
characters used to each become their own underscore, which the final
``.replace("_", " ")`` turned into visible repeated whitespace
(e.g. "Count   Lsp      Prompt")."""
from __future__ import annotations

from nuguard.sbom.normalization import humanize_context_name


def test_camel_case_split_into_title_case_words():
    assert humanize_context_name("systemPrompt") == "System Prompt"


def test_basic_camel_case_conversion():
    assert humanize_context_name("countLsp") == "Count Lsp"


def test_repeated_non_alnum_chars_collapse_to_single_space():
    assert humanize_context_name("count!!!lsp###extra") == "Count Lsp Extra"


def test_generic_single_word_returns_none():
    assert humanize_context_name("prompt") is None
    assert humanize_context_name("template") is None
    assert humanize_context_name("message") is None


def test_empty_context_returns_none():
    assert humanize_context_name("") is None
    assert humanize_context_name("   ") is None


def test_custom_generic_words():
    assert humanize_context_name("role", generic_words=frozenset({"role"})) is None
    assert humanize_context_name("role") == "Role"
