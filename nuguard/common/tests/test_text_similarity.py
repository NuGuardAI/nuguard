"""Tests for the shared Jaccard text-similarity primitives."""
from __future__ import annotations

from nuguard.common.text_similarity import extract_tokens, jaccard


def test_jaccard_identical_text_returns_1() -> None:
    tokens = extract_tokens("Unauthenticated GET /api/agents returned account data")
    assert jaccard(tokens, tokens) == 1.0


def test_jaccard_disjoint_text_returns_0() -> None:
    a = extract_tokens("unauthenticated agents topology leaked")
    b = extract_tokens("mass assignment field injection privilege escalation")
    assert jaccard(a, b) == 0.0


def test_jaccard_empty_sets_returns_0() -> None:
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_extract_tokens_filters_stopwords() -> None:
    tokens = extract_tokens("The agent will show your response and data")
    assert "the" not in tokens
    assert "will" not in tokens
    assert "agent" not in tokens
    assert "response" not in tokens
