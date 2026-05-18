"""Unit tests for nuguard/behavior/_utils.py."""
from __future__ import annotations

import pytest

from nuguard.behavior._utils import (
    extract_could_be_used_action,
    extract_json_object,
    is_not_used_response,
    mentioned_actively,
    normalise_name,
    parse_json_array,
    strip_markdown_fences,
)

# ---------------------------------------------------------------------------
# strip_markdown_fences
# ---------------------------------------------------------------------------


def test_strip_markdown_fences_plain():
    assert strip_markdown_fences("```\nhello\n```") == "hello"


def test_strip_markdown_fences_language():
    assert strip_markdown_fences("```json\n{\"a\": 1}\n```") == '{"a": 1}'


def test_strip_markdown_fences_no_fences():
    text = "plain text"
    assert strip_markdown_fences(text) == text


def test_strip_markdown_fences_empty():
    assert strip_markdown_fences("") == ""


# ---------------------------------------------------------------------------
# extract_json_object
# ---------------------------------------------------------------------------


def test_extract_json_object_simple():
    result = extract_json_object('{"key": "value"}')
    assert result == {"key": "value"}


def test_extract_json_object_embedded():
    result = extract_json_object('Some text before: {"x": 1} and after')
    assert result == {"x": 1}


def test_extract_json_object_fenced():
    result = extract_json_object('```json\n{"hello": "world"}\n```')
    assert result == {"hello": "world"}


def test_extract_json_object_invalid():
    result = extract_json_object("not json at all")
    assert result is None


def test_extract_json_object_empty():
    assert extract_json_object("") is None


def test_extract_json_object_nested():
    result = extract_json_object('{"a": {"b": [1, 2]}}')
    assert result == {"a": {"b": [1, 2]}}


# ---------------------------------------------------------------------------
# parse_json_array
# ---------------------------------------------------------------------------


def test_parse_json_array_simple():
    result = parse_json_array('["a", "b", "c"]')
    assert result == ["a", "b", "c"]


def test_parse_json_array_fenced():
    result = parse_json_array('```json\n["x", "y"]\n```')
    assert result == ["x", "y"]


def test_parse_json_array_dict_with_array():
    result = parse_json_array('{"items": ["one", "two"]}')
    assert result == ["one", "two"]


def test_parse_json_array_fallback_quoted_strings():
    # Fallback: extracts quoted strings >= 10 chars
    result = parse_json_array('"this is a long string" "another long one"')
    assert result is not None
    assert len(result) > 0


def test_parse_json_array_empty_string():
    result = parse_json_array("")
    # Should return None or empty
    assert result is None or result == []


# ---------------------------------------------------------------------------
# normalise_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("Ad Copy Writer", "adcopywriter"),
    ("AdCopyWriter", "adcopywriter"),
    ("ad_copy_writer", "adcopywriter"),
    ("ad-copy-writer", "adcopywriter"),
    ("", ""),
    ("SimpleAgent", "simpleagent"),
])
def test_normalise_name(name, expected):
    assert normalise_name(name) == expected


# ---------------------------------------------------------------------------
# is_not_used_response
# ---------------------------------------------------------------------------


def test_is_not_used_response_was_not_used():
    response = "The SearchTool was not used in this response."
    assert is_not_used_response(response, "SearchTool") is True


def test_is_not_used_response_wasnt_involved():
    response = "SearchTool wasn't involved in handling your request."
    assert is_not_used_response(response, "SearchTool") is True


def test_is_not_used_response_generic_no_tools():
    response = "No agents or tools were used to generate this response."
    assert is_not_used_response(response, "AnyTool") is True


def test_is_not_used_response_active_use():
    response = "I used SearchTool to find the information you requested."
    assert is_not_used_response(response, "SearchTool") is False


def test_is_not_used_response_component_not_mentioned():
    response = "I helped with your request using available capabilities."
    # Component not mentioned at all — check generic not-used patterns (unlikely here)
    # Should not be True for this generic response
    assert is_not_used_response(response, "SpecificTool") is False


def test_is_not_used_response_empty():
    assert is_not_used_response("", "tool") is False


# ---------------------------------------------------------------------------
# extract_could_be_used_action
# ---------------------------------------------------------------------------


def test_extract_could_be_used_action_found():
    response = "The search tool could be used to find relevant articles."
    result = extract_could_be_used_action(response)
    assert result is not None
    assert "find" in result.lower()


def test_extract_could_be_used_action_not_found():
    result = extract_could_be_used_action("No pattern here.")
    assert result is None


def test_extract_could_be_used_action_empty():
    assert extract_could_be_used_action("") is None


# ---------------------------------------------------------------------------
# mentioned_actively
# ---------------------------------------------------------------------------


def test_mentioned_actively_present():
    response = "I used SearchAgent to find results."
    assert mentioned_actively("SearchAgent", response) is True


def test_mentioned_actively_negated():
    response = "SearchAgent was not used in this request."
    assert mentioned_actively("SearchAgent", response) is False


def test_mentioned_actively_empty_component():
    assert mentioned_actively("", "some response") is False


def test_mentioned_actively_empty_response():
    assert mentioned_actively("Agent", "") is False


def test_mentioned_actively_component_not_in_response():
    assert mentioned_actively("MissingAgent", "I helped you with your task.") is False
