"""Tests for shared refusal-detection token matching.

Regression coverage for the smart-quote normalization bug: LLM output
frequently uses the U+2019 RIGHT SINGLE QUOTATION MARK (e.g. "can't") while
every token set in refusal_patterns.py is written with plain ASCII
apostrophes. Without normalization, substring matching silently misses a
large fraction of real refusals.
"""
from __future__ import annotations

from nuguard.redteam.llm_engine.adaptive_mutation import classify_failure
from nuguard.redteam.llm_engine.refusal_patterns import (
    ATTACKER_SELF_REFUSAL_TOKENS,
    HARD_REFUSAL_TOKENS,
    contains_any_token,
    is_attacker_self_refusal,
    normalize_for_matching,
)


class TestNormalizeForMatching:
    def test_lowercases(self) -> None:
        assert normalize_for_matching("I CANNOT Help") == "i cannot help"

    def test_normalizes_curly_single_quote(self) -> None:
        assert normalize_for_matching("I can’t help") == "i can't help"

    def test_normalizes_curly_double_quote(self) -> None:
        assert normalize_for_matching("she said “hello”") == 'she said "hello"'

    def test_empty_string(self) -> None:
        assert normalize_for_matching("") == ""


class TestContainsAnyToken:
    def test_matches_plain_apostrophe(self) -> None:
        assert contains_any_token("I can't help with that.", HARD_REFUSAL_TOKENS)

    def test_matches_curly_apostrophe(self) -> None:
        assert contains_any_token("I can’t help with that.", HARD_REFUSAL_TOKENS)

    def test_empty_text_returns_false(self) -> None:
        assert not contains_any_token("", HARD_REFUSAL_TOKENS)

    def test_no_match_returns_false(self) -> None:
        assert not contains_any_token("Here is your flight status.", HARD_REFUSAL_TOKENS)


class TestIsAttackerSelfRefusal:
    def test_detects_plain_apostrophe_refusal(self) -> None:
        assert is_attacker_self_refusal(
            "I can't help craft messages to bypass an airline assistant's guardrails."
        )

    def test_detects_curly_apostrophe_refusal(self) -> None:
        # Real gpt-5.4-mini output uses U+2019, not a straight apostrophe.
        assert is_attacker_self_refusal(
            "I can’t help plan or optimize a workflow to extract "
            "another customer’s PII."
        )

    def test_non_refusal_returns_false(self) -> None:
        assert not is_attacker_self_refusal(
            "Hi! I'd like to check the status of my flight AA1234."
        )

    def test_empty_string_returns_false(self) -> None:
        assert not is_attacker_self_refusal("")

    def test_every_token_is_lowercase(self) -> None:
        # contains_any_token lowercases the input but not the tokens — every
        # token must already be lowercase or matches will silently fail.
        assert all(tok == tok.lower() for tok in ATTACKER_SELF_REFUSAL_TOKENS)


class TestClassifyFailureCurlyQuotes:
    """classify_failure() (adaptive_mutation.py) drives mutation-strategy
    selection — a missed refusal here means the wrong follow-up strategy."""

    def test_hard_refusal_with_curly_apostrophe(self) -> None:
        assert classify_failure("I’m not able to generate that.") == "HARD_REFUSAL"

    def test_hard_refusal_with_plain_apostrophe(self) -> None:
        assert classify_failure("I'm not able to generate that.") == "HARD_REFUSAL"
