"""Unit tests for nuguard.common.browser_login.heuristics — pure functions/data,
no Playwright/browser required."""
from __future__ import annotations

from nuguard.common.browser_login import heuristics


class TestBuildCandidates:
    def test_configured_value_tried_first(self) -> None:
        result = heuristics.build_candidates("#custom", ["a", "b"])
        assert result == ["#custom", "a", "b"]

    def test_empty_configured_falls_back_to_defaults(self) -> None:
        result = heuristics.build_candidates("", ["a", "b"])
        assert result == ["a", "b"]

    def test_whitespace_only_configured_falls_back_to_defaults(self) -> None:
        result = heuristics.build_candidates("   ", ["a", "b"])
        assert result == ["a", "b"]

    def test_configured_value_is_stripped(self) -> None:
        result = heuristics.build_candidates("  #custom  ", ["a"])
        assert result == ["#custom", "a"]


class TestBuildTextCandidates:
    def test_configured_prepended_before_defaults(self) -> None:
        result = heuristics.build_text_candidates(["Log in"], ["sign in", "continue"])
        assert result == ["Log in", "sign in", "continue"]

    def test_case_insensitive_dedup_prefers_configured_casing(self) -> None:
        result = heuristics.build_text_candidates(["Sign In"], ["sign in", "continue"])
        assert result == ["Sign In", "continue"]

    def test_empty_configured_list_returns_defaults(self) -> None:
        result = heuristics.build_text_candidates([], ["log in", "sign in"])
        assert result == ["log in", "sign in"]

    def test_blank_entries_are_dropped(self) -> None:
        result = heuristics.build_text_candidates(["", "  "], ["log in"])
        assert result == ["log in"]


class TestDynamicPayloadFieldNames:
    def test_common_session_scoped_fields_are_excluded(self) -> None:
        for name in ("sessionID", "session_id", "conversationId", "requestId", "nonce"):
            assert name.lower() in heuristics.DYNAMIC_PAYLOAD_FIELD_NAMES

    def test_arbitrary_identity_field_is_not_excluded(self) -> None:
        assert "consumerid" not in heuristics.DYNAMIC_PAYLOAD_FIELD_NAMES
