"""Lightweight, embedding-free text-similarity primitives shared across nuguard.

Jaccard similarity over a stop-word-filtered token set is cheap, requires no
model calls, and works well for short, keyword-rich security text (attack
payloads, finding evidence). Extracted from
``nuguard.redteam.executor.similarity_miss_tracker`` so other callers (e.g.
finding-evidence dedup) can reuse the exact same tokenisation without
duplicating the stop-word list.
"""
from __future__ import annotations

import re

# Common English words + generic security/LLM scaffolding that appear in
# virtually every payload/evidence string and carry no discriminating signal.
SECURITY_STOP_WORDS: frozenset[str] = frozenset({
    # Common English
    "the", "and", "for", "this", "that", "with", "your", "are", "can", "not",
    "you", "have", "from", "will", "about", "when", "what", "how", "please",
    "would", "could", "like", "need", "want", "help", "just", "also", "was",
    "been", "has", "its", "our", "their", "they", "some", "all", "any", "use",
    "show", "give", "get", "let", "see", "now", "then", "but", "one", "two",
    "make", "sure", "tell", "said", "text", "output", "input", "based", "only",
    "here", "more", "very", "into", "than", "such", "each", "must", "may",
    # Generic AI/security scaffolding
    "system", "user", "message", "response", "request", "data", "information",
    "agent", "chat", "llm", "model", "api", "step", "task", "function",
    "prompt", "query", "answer", "result", "following", "below", "above",
    "provide", "using", "used", "send", "receive", "return", "call",
    "test", "example", "format", "include", "details",
})


def extract_tokens(text: str) -> frozenset[str]:
    """Lowercase, tokenise, and remove stop-words from *text*."""
    raw = re.findall(r"\b[a-z_][a-z0-9_]{2,}\b", text.lower())
    return frozenset(t for t in raw if t not in SECURITY_STOP_WORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Return the Jaccard similarity of two token sets (0.0 when both empty)."""
    union = len(a | b)
    return len(a & b) / union if union else 0.0
