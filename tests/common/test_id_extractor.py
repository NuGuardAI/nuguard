"""Tests for markdown-tolerant golden-data extraction in nuguard.common.id_extractor."""
from __future__ import annotations

from nuguard.common.id_extractor import (
    extract_customer_name,
    extract_entity_map,
    extract_ids,
)


def test_extract_customer_name_plain_label() -> None:
    assert extract_customer_name("Account holder: Alice Johnson.") == "Alice Johnson"


def test_extract_customer_name_markdown_bold_label() -> None:
    # LLM chat agents commonly bold the label: "**Account Holder Name:** Alice Johnson"
    text = "Here is your profile:\n- **Account Holder Name:** Alice Johnson\n- **Account ID:** ACCT-001"
    assert extract_customer_name(text) == "Alice Johnson"


def test_extract_customer_name_markdown_bold_name_only_label() -> None:
    text = "**Name:** Bob Smith"
    assert extract_customer_name(text) == "Bob Smith"


def test_extract_ids_markdown_bold_label() -> None:
    text = "- **Account ID:** ACCT-001\n- **Balance:** $50,000.00"
    assert "ACCT-001" in extract_ids(text)


def test_extract_entity_map_markdown_bold_label() -> None:
    text = "**Flight:** BA205, **Seat:** 14A"
    entities = extract_entity_map(text)
    assert entities.get("flight") == "BA205"
    assert entities.get("seat") == "14A"
