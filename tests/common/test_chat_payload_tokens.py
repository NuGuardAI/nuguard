"""Unit tests for nuguard.common.chat_payload_tokens."""
from __future__ import annotations

from nuguard.common.chat_payload_tokens import (
    CONVERSATION_ID,
    EXTRAS_RECOGNIZED,
    HISTORY,
    MESSAGE,
    SESSION_ID,
    contains_any,
    find_unrecognized,
    max_depth,
    substitute,
)


class TestMaxDepth:
    def test_scalar_is_depth_zero(self) -> None:
        assert max_depth("hello") == 0
        assert max_depth(42) == 0
        assert max_depth(None) == 0
        assert max_depth(True) == 0

    def test_flat_dict_is_depth_one(self) -> None:
        assert max_depth({"user_id": "alice", "language": "en"}) == 1

    def test_flat_list_is_depth_one(self) -> None:
        assert max_depth(["a", "b"]) == 1

    def test_empty_dict_and_list_are_depth_one(self) -> None:
        assert max_depth({}) == 1
        assert max_depth([]) == 1

    def test_nested_dict_is_depth_two(self) -> None:
        assert max_depth({"message": {"role": "user", "content": "{{message}}"}}) == 2

    def test_deeply_nested_structure(self) -> None:
        node = {"a": {"b": {"c": ["d", {"e": "f"}]}}}
        assert max_depth(node) == 5

    def test_nested_list_of_dicts(self) -> None:
        assert max_depth({"messages": [{"role": "user", "content": "hi"}]}) == 3


class TestSubstitute:
    def test_exact_token_replaced(self) -> None:
        context = {MESSAGE: "hello world"}
        assert substitute(MESSAGE, context) == "hello world"

    def test_non_token_string_unchanged(self) -> None:
        context = {MESSAGE: "hello world"}
        assert substitute("plain string", context) == "plain string"

    def test_token_embedded_in_larger_string_not_substituted(self) -> None:
        context = {MESSAGE: "hello world"}
        assert substitute("Hi {{message}}", context) == "Hi {{message}}"

    def test_nested_dict_substitution(self) -> None:
        context = {MESSAGE: "hello", CONVERSATION_ID: "conv-1"}
        node = {
            "message": {"role": "user", "content": MESSAGE},
            "conversation_id": CONVERSATION_ID,
            "stream": False,
        }
        assert substitute(node, context) == {
            "message": {"role": "user", "content": "hello"},
            "conversation_id": "conv-1",
            "stream": False,
        }

    def test_list_substitution(self) -> None:
        context = {MESSAGE: "hello"}
        assert substitute([MESSAGE, "static"], context) == ["hello", "static"]

    def test_token_can_resolve_to_non_string_value(self) -> None:
        history = [{"role": "user", "content": "hi"}]
        context = {HISTORY: history}
        assert substitute({"history": HISTORY}, context) == {"history": history}

    def test_dict_keys_are_never_substituted(self) -> None:
        context = {MESSAGE: "hello"}
        # A token used as a dict key is not a documented/supported shape;
        # substitute() only ever touches values.
        assert substitute({MESSAGE: "value"}, context) == {MESSAGE: "value"}

    def test_missing_token_in_context_left_unchanged(self) -> None:
        assert substitute(SESSION_ID, {}) == SESSION_ID

    def test_token_resolving_to_none_drops_dict_key(self) -> None:
        context = {MESSAGE: "hello", CONVERSATION_ID: None}
        node = {
            "message": {"role": "user", "content": MESSAGE},
            "conversation_id": CONVERSATION_ID,
            "stream": False,
        }
        assert substitute(node, context) == {
            "message": {"role": "user", "content": "hello"},
            "stream": False,
        }

    def test_token_resolving_to_none_drops_list_item(self) -> None:
        context = {MESSAGE: "hello", CONVERSATION_ID: None}
        assert substitute([MESSAGE, CONVERSATION_ID, "static"], context) == ["hello", "static"]

    def test_explicit_none_value_not_a_token_is_untouched(self) -> None:
        context = {MESSAGE: "hello"}
        assert substitute({"language": None, "content": MESSAGE}, context) == {
            "language": None,
            "content": "hello",
        }

    def test_token_resolving_to_none_drops_nested_key(self) -> None:
        context = {MESSAGE: "hello", SESSION_ID: None}
        node = {"outer": {"message": MESSAGE, "session_id": SESSION_ID}}
        assert substitute(node, context) == {"outer": {"message": "hello"}}


class TestContainsAny:
    def test_detects_exact_token_value(self) -> None:
        assert contains_any({"user_id": SESSION_ID}, EXTRAS_RECOGNIZED)

    def test_no_tokens_present(self) -> None:
        assert not contains_any({"user_id": "alice"}, EXTRAS_RECOGNIZED)


class TestFindUnrecognized:
    def test_recognized_tokens_pass(self) -> None:
        node = {"message": {"role": "user", "content": MESSAGE}}
        assert find_unrecognized(node, EXTRAS_RECOGNIZED) == set()

    def test_typo_token_flagged(self) -> None:
        node = {"content": "{{mesage}}"}
        assert find_unrecognized(node, EXTRAS_RECOGNIZED) == {"{{mesage}}"}

    def test_unrecognized_token_embedded_in_larger_string_flagged(self) -> None:
        node = {"content": "Hi {{mesage}}"}
        assert find_unrecognized(node, EXTRAS_RECOGNIZED) == {"{{mesage}}"}
