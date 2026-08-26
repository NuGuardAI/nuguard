"""Regression tests for Fix 3: boilerplate stripping + relaxed topic-refusal anchoring.

Some target apps (e.g. phlox) prefix nearly every response with literal
wrapper/error boilerplate such as
``"Error processing request. Generating direct response..."``. This doesn't
corrupt unanchored substring matching, but it DOES break the ``^``-anchored
``_TOPIC_REFUSAL_PATTERNS`` regexes in
``nuguard.redteam.llm_engine.response_extractor``, wrongly keeping
``attack_class_viable=True`` when the agent actually hard-refused on-topic.

Two independent layers now guard against this:

1. ``nuguard.common.transport.strip_known_boilerplate`` strips known
   boilerplate prefixes once, at the ``TargetAppClient`` choke point, before
   any downstream consumer (including this extractor) ever sees the text.
2. As defense-in-depth, ``_TOPIC_REFUSAL_PATTERNS`` are now anchored to
   "start of response OR start of a sentence" rather than strictly "start of
   response", so an *unknown* boilerplate prefix (not in the strip list)
   still doesn't silently defeat topic-refusal detection.
"""
from __future__ import annotations

from nuguard.common.transport import strip_known_boilerplate
from nuguard.redteam.llm_engine.response_extractor import (
    _is_topic_refusal,
    extract_turn_facts,
)

_BOILERPLATE = "Error processing request. Generating direct response..."
_REFUSAL = "Sorry, I can only help with booking-related questions."


def test_strip_known_boilerplate_single_occurrence() -> None:
    text = f"{_BOILERPLATE} {_REFUSAL}"
    assert strip_known_boilerplate(text) == _REFUSAL


def test_strip_known_boilerplate_repeated_occurrences() -> None:
    """The boilerplate banner sometimes repeats 2-3 times in real responses."""
    text = f"{_BOILERPLATE} {_BOILERPLATE} {_BOILERPLATE} {_REFUSAL}"
    assert strip_known_boilerplate(text) == _REFUSAL


def test_strip_known_boilerplate_leaves_unrelated_text_unchanged() -> None:
    text = "I'd be happy to help you book a flight to Paris."
    assert strip_known_boilerplate(text) == text


def test_strip_known_boilerplate_all_boilerplate_keeps_text() -> None:
    """If the entire response IS the boilerplate, don't strip it to an empty string."""
    assert strip_known_boilerplate(_BOILERPLATE) == _BOILERPLATE


def test_strip_known_boilerplate_empty_string() -> None:
    assert strip_known_boilerplate("") == ""


def test_topic_refusal_detected_after_stripping_boilerplate() -> None:
    """Once the boilerplate is stripped (as TargetAppClient now does at the
    choke point), the anchored refusal pattern matches at position 0 again."""
    stripped = strip_known_boilerplate(f"{_BOILERPLATE} {_REFUSAL}")
    assert _is_topic_refusal(stripped) is True


def test_topic_refusal_relaxed_anchoring_survives_unstripped_boilerplate() -> None:
    """Defense-in-depth: an unknown/unstripped boilerplate-style prefix must not
    silently defeat topic-refusal detection — the pattern also matches at the
    start of a sentence, not only byte 0 of the string."""
    unknown_prefix = "System notice: retrying after internal timeout."
    text = f"{unknown_prefix} {_REFUSAL}"
    assert _is_topic_refusal(text) is True


def test_topic_refusal_not_falsely_matched_mid_sentence() -> None:
    """The relaxed anchoring must still not match a refusal phrase quoted
    mid-sentence inside a longer engaged response (the false-positive risk
    the original '^'-only anchoring was designed to avoid)."""
    text = (
        "Here is the flight information you asked about: flight BA256 departs "
        "at 10am from gate 22, and by the way the phrase sorry, I can only "
        "help with that is just something our old bot used to say."
    )
    assert _is_topic_refusal(text) is False


def test_extract_turn_facts_retires_attack_class_with_boilerplate_prefix() -> None:
    """End-to-end: extract_turn_facts still correctly retires the attack class
    (attack_class_viable=False) for a genuine topic refusal even with a
    boilerplate prefix attached, via the relaxed sentence-start anchoring."""
    text = f"{_BOILERPLATE} {_REFUSAL}"
    facts = extract_turn_facts(text)
    assert facts.agent_posture == "hard_refused"
    assert facts.attack_class_viable is False


def test_extract_turn_facts_engaged_response_unaffected() -> None:
    """A genuinely engaged response (no refusal) is unaffected by the relaxed
    anchoring — no false-positive retirement."""
    text = (
        "Sure! Your booking reference is ABC123 and your flight departs from "
        "gate 14 at 3pm. Let me know if you need anything else."
    )
    facts = extract_turn_facts(text)
    assert facts.agent_posture == "engaged"
    assert facts.attack_class_viable is True
