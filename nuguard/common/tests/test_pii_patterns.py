"""Tests for the shared PII-shaped-value regex patterns.

Regression for a false-positive flood observed against OWASP Juice Shop's
redteam run: every unauthenticated request correctly got HTTP 401, but the
stock Express error page's own banner — "OWASP Juice Shop (Express
^4.22.1)" — matched the "Full Name" pattern's original lookahead (any two
capitalized words followed by a parenthesis), feeding a false "PII-shaped
values" signal into the LLM evaluator and flipping ~35 correctly-enforced
auth checks into "attack succeeded" findings.
"""
from __future__ import annotations

from nuguard.common.pii_patterns import find_pii_values


def test_app_banner_followed_by_parenthesis_not_matched_as_name() -> None:
    text = "<h1>OWASP Juice Shop (Express ^4.22.1)</h1>"
    assert find_pii_values(text) == []


def test_framework_version_banner_not_matched() -> None:
    text = "Error Page (Internal Server Error)"
    assert find_pii_values(text) == []


def test_name_followed_by_is_still_matched() -> None:
    text = "Jane Smith is the account holder for this record."
    hits = find_pii_values(text)
    assert "Jane Smith" in hits


def test_name_followed_by_possessive_still_matched() -> None:
    text = "John Doe's balance is overdue."
    hits = find_pii_values(text)
    assert "John Doe" in hits


def test_name_followed_by_has_still_matched() -> None:
    text = "Mary Jones has requested a refund."
    hits = find_pii_values(text)
    assert "Mary Jones" in hits
