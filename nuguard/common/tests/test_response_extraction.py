"""Unit tests for nuguard/common/response_extraction.py."""
from __future__ import annotations

from nuguard.common.response_extraction import build_minimal_payload, extract_response_id


class TestExtractResponseId:
    def test_known_session_key_found(self) -> None:
        assert extract_response_id({"conversation_id": "c_1"}) == "c_1"

    def test_first_matching_key_wins_in_priority_order(self) -> None:
        assert extract_response_id({"chat_id": "c_2", "session_id": "s_1"}) == "s_1"

    def test_extra_keys_checked_after_default_keys(self) -> None:
        assert extract_response_id({"id": "c_3"}, extra_keys=("id",)) == "c_3"

    def test_extra_key_not_used_when_not_requested(self) -> None:
        assert extract_response_id({"id": "c_3"}) is None

    def test_no_match_returns_none(self) -> None:
        assert extract_response_id({"foo": "bar"}) is None

    def test_non_dict_returns_none(self) -> None:
        assert extract_response_id("not a dict") is None
        assert extract_response_id(None) is None

    def test_null_value_skipped(self) -> None:
        assert extract_response_id({"session_id": None, "id": "c_4"}, extra_keys=("id",)) == "c_4"

    def test_non_string_value_stringified(self) -> None:
        assert extract_response_id({"id": 42}, extra_keys=("id",)) == "42"


class TestBuildMinimalPayload:
    def test_empty_schema_returns_empty_dict(self) -> None:
        assert build_minimal_payload({}) == {}

    def test_field_name_hint_takes_priority(self) -> None:
        body = build_minimal_payload({"email": "str"})
        assert body["email"] == "testuser@example.com"

    def test_type_fallback_used_when_no_field_hint(self) -> None:
        body = build_minimal_payload({"is_active": "bool"})
        assert body["is_active"] is True

    def test_unknown_type_falls_back_to_test_value(self) -> None:
        body = build_minimal_payload({"widget": "CustomType"})
        assert body["widget"] == "test-value"

    def test_int_and_list_and_dict_fallbacks(self) -> None:
        body = build_minimal_payload({"count": "int", "tags": "list", "meta": "dict"})
        assert body == {"count": 1, "tags": [], "meta": {}}
