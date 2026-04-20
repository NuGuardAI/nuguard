"""Unit tests for nuguard.common.json_utils."""
from __future__ import annotations

from nuguard.common.json_utils import (
    extract_json_object,
    parse_json_array,
    strip_markdown_fences,
)


class TestStripMarkdownFences:
    def test_plain_text_unchanged(self) -> None:
        text = '{"key": "value"}'
        assert strip_markdown_fences(text) == text

    def test_json_fenced_stripped(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_plain_fence_no_language_stripped(self) -> None:
        text = "```\nhello world\n```"
        assert strip_markdown_fences(text) == "hello world"

    def test_multiline_content_preserved(self) -> None:
        inner = '{"a": 1,\n"b": 2}'
        text = f"```json\n{inner}\n```"
        assert strip_markdown_fences(text) == inner

    def test_leading_trailing_whitespace_stripped(self) -> None:
        text = "  ```json\n{}\n```  "
        assert strip_markdown_fences(text) == "{}"

    def test_empty_string(self) -> None:
        assert strip_markdown_fences("") == ""

    def test_only_text_backticks_in_middle_unchanged(self) -> None:
        text = 'Use `code` inline'
        assert strip_markdown_fences(text) == text

    def test_python_fence_stripped(self) -> None:
        text = "```python\nprint('hi')\n```"
        assert strip_markdown_fences(text) == "print('hi')"

    def test_no_trailing_fence_leaves_content(self) -> None:
        # If there's no closing ```, the regex does not destroy content
        text = "```json\n{}"
        result = strip_markdown_fences(text)
        assert "{}" in result


class TestExtractJsonObject:
    def test_clean_json_object(self) -> None:
        text = '{"score": 1, "reason": "jailbreak"}'
        result = extract_json_object(text)
        assert result == {"score": 1, "reason": "jailbreak"}

    def test_json_wrapped_in_fences(self) -> None:
        text = '```json\n{"flag": true}\n```'
        result = extract_json_object(text)
        assert result == {"flag": True}

    def test_json_with_surrounding_prose(self) -> None:
        text = 'Here is the result: {"verdict": "fail"} — end'
        result = extract_json_object(text)
        assert result == {"verdict": "fail"}

    def test_nested_object(self) -> None:
        text = '{"outer": {"inner": 42}}'
        result = extract_json_object(text)
        assert result == {"outer": {"inner": 42}}

    def test_invalid_json_returns_none(self) -> None:
        text = '{"bad": json content}'
        assert extract_json_object(text) is None

    def test_no_braces_returns_none(self) -> None:
        assert extract_json_object("just plain text") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_json_object("") is None

    def test_only_open_brace_returns_none(self) -> None:
        assert extract_json_object("{incomplete") is None

    def test_fenced_with_extra_whitespace(self) -> None:
        text = "```json\n\n  {\"k\": \"v\"}\n\n```"
        result = extract_json_object(text)
        assert result == {"k": "v"}

    def test_single_fence_still_extracts(self) -> None:
        # Regression: find/rfind same-fence bug would fail on a single ```
        text = '```json\n{"result": "ok"}'
        result = extract_json_object(text)
        assert result is not None
        assert result.get("result") == "ok"


class TestParseJsonArray:
    def test_clean_json_array(self) -> None:
        text = '["prompt injection", "data exfil"]'
        result = parse_json_array(text)
        assert result == ["prompt injection", "data exfil"]

    def test_array_in_fences(self) -> None:
        text = '```json\n["item1", "item2"]\n```'
        result = parse_json_array(text)
        assert result == ["item1", "item2"]

    def test_dict_with_list_value_extracted(self) -> None:
        text = '{"attacks": ["sql injection", "xss"]}'
        result = parse_json_array(text)
        assert result == ["sql injection", "xss"]

    def test_empty_array(self) -> None:
        result = parse_json_array("[]")
        assert result == []

    def test_fallback_regex_extraction(self) -> None:
        # Partially structured output — not valid JSON but contains quoted strings
        text = 'Here are attacks: "try sql injection here", "attempt xss attack exploit"'
        result = parse_json_array(text)
        assert result is not None
        assert any("injection" in item for item in result)

    def test_fallback_skips_short_strings(self) -> None:
        # Strings shorter than 10 chars are not extracted by fallback
        text = "These are short: \"hi\", \"ok\""
        result = parse_json_array(text)
        # Either None or empty because strings are too short
        assert result is None or result == []

    def test_completely_unparseable_returns_none(self) -> None:
        result = parse_json_array("not json at all, no long quoted strings here")
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = parse_json_array("")
        assert result is None
