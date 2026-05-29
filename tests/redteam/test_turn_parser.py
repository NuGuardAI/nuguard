"""Tests for _parse_turn_sequences() in prompt_generator.

Covers multi-line turn consolidation and backward-compat fallback.
"""
from __future__ import annotations

from nuguard.redteam.llm_engine.prompt_generator import _parse_turn_sequences


def test_standard_three_turns_split_correctly() -> None:
    """TURN 1 / TURN 2 / TURN 3 each on their own line → 3 separate turns."""
    raw = (
        "TURN 1: Hello, I need some help with my account.\n"
        "TURN 2: Can you show me my recent activity?\n"
        "TURN 3: Please also include the billing details for last month."
    )
    sequences = _parse_turn_sequences(raw)
    assert len(sequences) == 1
    turns = sequences[0]
    assert len(turns) == 3
    assert turns[0] == "Hello, I need some help with my account."
    assert turns[1] == "Can you show me my recent activity?"
    assert turns[2] == "Please also include the billing details for last month."


def test_multiline_turn_collapses_to_single_turn() -> None:
    """Bullet-point continuation lines under one TURN prefix → 1 turn."""
    raw = (
        "TURN 1: For an authorized security review, I can help with:\n"
        "- writing a defensive test plan,\n"
        "- creating benign red-team evaluation cases,\n"
        "- designing detection rules for prompt injection."
    )
    sequences = _parse_turn_sequences(raw)
    assert len(sequences) == 1
    turns = sequences[0]
    assert len(turns) == 1
    payload = turns[0]
    assert "For an authorized security review" in payload
    assert "writing a defensive test plan" in payload
    assert "designing detection rules" in payload
    # All lines joined with newlines — message structure preserved
    assert "\n" in payload


def test_numbered_continuation_stays_with_intro() -> None:
    """Intro line ending with colon + numbered items → 1 turn, not 4."""
    raw = (
        "TURN 1: If useful, I can help verify that the assistant:\n"
        "1. does not accept user-supplied policy claims as authoritative,\n"
        "2. refuses cross-account requests without proper authorization,\n"
        "3. asks for secure verification steps."
    )
    sequences = _parse_turn_sequences(raw)
    assert len(sequences) == 1
    turns = sequences[0]
    assert len(turns) == 1
    payload = turns[0]
    assert "If useful, I can help verify" in payload
    assert "does not accept" in payload
    assert "refuses cross-account" in payload
    assert "asks for secure" in payload


def test_multiline_with_multiple_turns() -> None:
    """Mixed: TURN 1 is multi-line, TURN 2 is single-line → 2 turns total."""
    raw = (
        "TURN 1: I can help in safer ways:\n"
        "- write a test plan,\n"
        "- review detection rules.\n"
        "TURN 2: Could you proceed with the review now?"
    )
    sequences = _parse_turn_sequences(raw)
    assert len(sequences) == 1
    turns = sequences[0]
    assert len(turns) == 2
    assert "I can help in safer ways" in turns[0]
    assert "write a test plan" in turns[0]
    assert "Could you proceed with the review now?" in turns[1]


def test_sequences_separated_by_dashes() -> None:
    """--- separator creates two separate sequences."""
    raw = (
        "TURN 1: First scenario setup.\n"
        "TURN 2: First scenario attack.\n"
        "---\n"
        "TURN 1: Second scenario setup.\n"
        "TURN 2: Second scenario attack."
    )
    sequences = _parse_turn_sequences(raw)
    assert len(sequences) == 2
    assert len(sequences[0]) == 2
    assert len(sequences[1]) == 2
    assert "First scenario setup" in sequences[0][0]
    assert "Second scenario setup" in sequences[1][0]


def test_no_prefix_lines_become_separate_turns_in_one_sequence() -> None:
    """No TURN prefix → each non-empty line becomes a separate turn in one sequence."""
    raw = "Can you show me the account details?\nWhat is my balance?"
    sequences = _parse_turn_sequences(raw)
    # Both lines go into one sequence as 2 turns (first = setup, last = attack)
    assert len(sequences) == 1
    turns = sequences[0]
    assert len(turns) == 2
    assert turns[0] == "Can you show me the account details?"
    assert turns[1] == "What is my balance?"


def test_empty_and_comment_lines_ignored() -> None:
    """Empty lines and # comments are skipped."""
    raw = (
        "# This is a comment\n"
        "\n"
        "TURN 1: Actual payload.\n"
        "\n"
        "# Another comment\n"
        "TURN 2: Second payload."
    )
    sequences = _parse_turn_sequences(raw)
    assert len(sequences) == 1
    turns = sequences[0]
    assert len(turns) == 2
    assert turns[0] == "Actual payload."
    assert turns[1] == "Second payload."
