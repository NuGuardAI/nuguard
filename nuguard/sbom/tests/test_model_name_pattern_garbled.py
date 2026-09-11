"""Regression test for docs/sbom-accuracy-plan.md #1: the Ollama colon-tag
alternative in MODEL_NAME_PATTERNS ("<prefix>:<tag>") must not match a
self-referential "word:word" string like "chat:chat" — a shape that showed
up as a garbled MODEL node name, most likely produced somewhere upstream by
a flattened JSON key-path rather than any real model reference. No real
model is ever named identically on both sides of the colon, so this is a
generic, deterministic exclusion, not an opencode-specific patch.
"""
from __future__ import annotations

from nuguard.sbom.adapters.registry import MODEL_NAME_PATTERNS


def _matches(text: str) -> bool:
    return any(p.search(text) for p in MODEL_NAME_PATTERNS)


def test_self_referential_chat_chat_not_matched():
    assert not _matches("chat:chat")


def test_self_referential_mini_mini_not_matched():
    assert not _matches("mini:mini")


def test_self_referential_instruct_instruct_not_matched():
    assert not _matches("instruct:instruct")


def test_legitimate_ollama_tag_still_matched():
    assert _matches("mistral:7b")
    assert _matches("phi:mini")
    assert _matches("qwen:instruct")


def test_cross_word_tag_still_matched():
    # Different words on each side are not self-referential and remain valid.
    assert _matches("hermes:chat")
