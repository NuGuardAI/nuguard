"""Unit tests for NUGUARD_LOG_HTTP_BODIES opt-in request/response body tracing.

nuguard.common.logging.get_logger() sets propagate=False, so caplog (which
listens on the root logger) never sees these records — assert against the
module logger directly instead.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from nuguard.redteam.target.client import _log_http_request_body, _log_http_response_body


class TestLogHttpRequestBody:
    def test_silent_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NUGUARD_LOG_HTTP_BODIES", raising=False)
        with patch("nuguard.redteam.target.client._log") as log_mock:
            _log_http_request_body("POST", "/chat", {"message": "hi"})
        log_mock.info.assert_not_called()

    def test_echoes_body_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")
        with patch("nuguard.redteam.target.client._log") as log_mock:
            _log_http_request_body("POST", "/chat", {"message": "hi"})
        log_mock.info.assert_called_once()
        rendered = log_mock.info.call_args.args
        message = rendered[0] % rendered[1:]
        assert message.startswith("[NUGUARD_LOG_HTTP_BODIES] HTTP POST /chat:")
        assert '"message": "hi"' in message

    def test_label_suffix_distinguishes_stream_requests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")
        with patch("nuguard.redteam.target.client._log") as log_mock:
            _log_http_request_body("POST", "/chat", {"message": "hi"}, label="(stream)")
        rendered = log_mock.info.call_args.args
        message = rendered[0] % rendered[1:]
        assert message.startswith("[NUGUARD_LOG_HTTP_BODIES] HTTP POST /chat (stream):")


class TestLogHttpResponseBody:
    def test_silent_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NUGUARD_LOG_HTTP_BODIES", raising=False)
        with patch("nuguard.redteam.target.client._log") as log_mock:
            _log_http_response_body(200, "/chat", {"response": "ok"})
        log_mock.info.assert_not_called()

    def test_echoes_body_with_status_code_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")
        with patch("nuguard.redteam.target.client._log") as log_mock:
            _log_http_response_body(200, "/chat", {"response": "ok"})
        log_mock.info.assert_called_once()
        rendered = log_mock.info.call_args.args
        message = rendered[0] % rendered[1:]
        assert message.startswith("[NUGUARD_LOG_HTTP_BODIES] HTTP Response 200 /chat:")
        assert '"response": "ok"' in message

    def test_request_and_response_labels_are_distinguishable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The two label prefixes ("HTTP POST" vs "HTTP Response <code>") must
        never collide, so a reader can tell sent vs received apart at a glance."""
        monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")
        with patch("nuguard.redteam.target.client._log") as log_mock:
            _log_http_request_body("POST", "/chat", {"message": "hi"})
            _log_http_response_body(200, "/chat", {"response": "ok"})
        first_args = log_mock.info.call_args_list[0].args
        second_args = log_mock.info.call_args_list[1].args
        first_message = first_args[0] % first_args[1:]
        second_message = second_args[0] % second_args[1:]
        assert "HTTP POST" in first_message
        assert "HTTP Response" in second_message

    def test_falls_back_to_str_for_non_json_serializable_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A circular reference can't be json.dumps'd even with default=str
        (ValueError: Circular reference detected) — must not raise, must still log."""
        monkeypatch.setenv("NUGUARD_LOG_HTTP_BODIES", "1")

        circular: dict = {}
        circular["self"] = circular

        with patch("nuguard.redteam.target.client._log") as log_mock:
            _log_http_response_body(200, "/chat", circular)
        log_mock.info.assert_called_once()
