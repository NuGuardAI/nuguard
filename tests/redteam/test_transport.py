"""Unit tests for nuguard.common.transport.classify_transport."""
from __future__ import annotations

import json

import pytest

from nuguard.common.transport import (
    APP_TRANSIENT_ERROR_PATTERNS,
    RETRIABLE_OUTCOMES,
    TransportOutcome,
    classify_transport,
)


# ---------------------------------------------------------------------------
# Sentinel-prefix branches
# ---------------------------------------------------------------------------

class TestSentinelPrefixes:
    def test_429(self):
        assert classify_transport("[HTTP 429] rate limited") == TransportOutcome.RATE_LIMIT

    def test_429_no_trailing_text(self):
        assert classify_transport("[HTTP 429]") == TransportOutcome.RATE_LIMIT

    def test_500(self):
        assert classify_transport("[HTTP 500] internal server error") == TransportOutcome.HTTP_5XX

    def test_501(self):
        assert classify_transport("[HTTP 501]") == TransportOutcome.HTTP_5XX

    def test_502(self):
        assert classify_transport("[HTTP 502] bad gateway") == TransportOutcome.HTTP_GATEWAY_ERROR

    def test_503(self):
        assert classify_transport("[HTTP 503] service unavailable") == TransportOutcome.HTTP_GATEWAY_ERROR

    def test_504(self):
        assert classify_transport("[HTTP 504] gateway timeout") == TransportOutcome.HTTP_GATEWAY_ERROR

    def test_400(self):
        assert classify_transport("[HTTP 400] bad request") == TransportOutcome.HTTP_4XX

    def test_401(self):
        assert classify_transport("[HTTP 401] unauthorized") == TransportOutcome.HTTP_4XX

    def test_403(self):
        assert classify_transport("[HTTP 403] forbidden") == TransportOutcome.HTTP_4XX

    def test_404(self):
        assert classify_transport("[HTTP 404] not found") == TransportOutcome.HTTP_4XX

    def test_405(self):
        assert classify_transport("[HTTP 405] method not allowed") == TransportOutcome.HTTP_4XX

    def test_422(self):
        assert classify_transport("[HTTP 422] unprocessable") == TransportOutcome.HTTP_4XX

    def test_request_error(self):
        assert classify_transport("[REQUEST_ERROR: Connection refused]") == TransportOutcome.REQUEST_ERROR

    def test_request_error_long(self):
        assert classify_transport("[REQUEST_ERROR: DNS resolution failed for host xyz]") == TransportOutcome.REQUEST_ERROR


# ---------------------------------------------------------------------------
# OK (normal chat responses)
# ---------------------------------------------------------------------------

class TestOkResponses:
    def test_normal_text(self):
        assert classify_transport("Hello! How can I assist you today?") == TransportOutcome.OK

    def test_json_success(self):
        assert classify_transport('{"message": "done"}') == TransportOutcome.OK

    def test_empty_string(self):
        assert classify_transport("") == TransportOutcome.OK

    def test_number_string(self):
        assert classify_transport("42") == TransportOutcome.OK

    def test_http_prefix_lowercase_not_matched(self):
        # Only brackets+uppercase sentinel format is matched
        assert classify_transport("http 500 error") == TransportOutcome.OK


# ---------------------------------------------------------------------------
# App-transient phrase matching
# ---------------------------------------------------------------------------

class TestPhraseMatching:
    @pytest.mark.parametrize("phrase", sorted(APP_TRANSIENT_ERROR_PATTERNS))
    def test_exact_phrase(self, phrase: str):
        assert classify_transport(phrase) == TransportOutcome.APP_TRANSIENT

    @pytest.mark.parametrize("phrase", sorted(APP_TRANSIENT_ERROR_PATTERNS))
    def test_phrase_embedded_in_sentence(self, phrase: str):
        response = f"We're sorry, {phrase}. Please contact support."
        assert classify_transport(response) == TransportOutcome.APP_TRANSIENT

    @pytest.mark.parametrize("phrase", sorted(APP_TRANSIENT_ERROR_PATTERNS))
    def test_phrase_case_insensitive(self, phrase: str):
        assert classify_transport(phrase.upper()) == TransportOutcome.APP_TRANSIENT

    def test_phrase_not_triggered_on_sentinel(self):
        # A 5xx sentinel should classify as HTTP_5XX even if the message body
        # contains a transient phrase (the prefix wins).
        response = "[HTTP 503] service is temporarily unavailable"
        assert classify_transport(response) == TransportOutcome.HTTP_GATEWAY_ERROR


# ---------------------------------------------------------------------------
# JSON structured error bodies
# ---------------------------------------------------------------------------

class TestJsonBodies:
    def _j(self, d: dict) -> str:
        return json.dumps(d)

    def test_retryable_true(self):
        assert classify_transport(self._j({"retryable": True, "message": "try later"})) == TransportOutcome.APP_TRANSIENT

    def test_retryable_false_is_ok(self):
        assert classify_transport(self._j({"retryable": False})) == TransportOutcome.OK

    def test_error_string_temporarily_unavailable(self):
        assert classify_transport(self._j({"error": "temporarily_unavailable"})) == TransportOutcome.APP_TRANSIENT

    def test_error_string_service_unavailable(self):
        assert classify_transport(self._j({"error": "service_unavailable"})) == TransportOutcome.APP_TRANSIENT

    def test_error_string_unavailable(self):
        assert classify_transport(self._j({"error": "unavailable"})) == TransportOutcome.APP_TRANSIENT

    def test_error_dict_code_service_unavailable(self):
        assert classify_transport(self._j({"error": {"code": "SERVICE_UNAVAILABLE"}})) == TransportOutcome.APP_TRANSIENT

    def test_error_dict_code_gateway_error(self):
        assert classify_transport(self._j({"error": {"code": "GATEWAY_ERROR"}})) == TransportOutcome.APP_TRANSIENT

    def test_status_unavailable(self):
        assert classify_transport(self._j({"status": "unavailable"})) == TransportOutcome.APP_TRANSIENT

    def test_code_service_unavailable(self):
        assert classify_transport(self._j({"code": "SERVICE_UNAVAILABLE"})) == TransportOutcome.APP_TRANSIENT

    def test_normal_json_is_ok(self):
        assert classify_transport(self._j({"answer": "The capital of France is Paris."})) == TransportOutcome.OK

    def test_error_string_other(self):
        assert classify_transport(self._j({"error": "invalid_request"})) == TransportOutcome.OK

    def test_invalid_json_falls_through_to_phrase(self):
        # Malformed JSON that happens to contain a transient phrase should
        # fall through to phrase matching.
        response = '{having difficulty connecting'
        assert classify_transport(response) == TransportOutcome.APP_TRANSIENT

    def test_json_with_leading_whitespace(self):
        assert classify_transport("  " + self._j({"retryable": True})) == TransportOutcome.APP_TRANSIENT


# ---------------------------------------------------------------------------
# RETRIABLE_OUTCOMES constant
# ---------------------------------------------------------------------------

class TestRetriableOutcomes:
    def test_app_transient_is_retriable(self):
        assert TransportOutcome.APP_TRANSIENT in RETRIABLE_OUTCOMES

    def test_http_gateway_error_is_retriable(self):
        assert TransportOutcome.HTTP_GATEWAY_ERROR in RETRIABLE_OUTCOMES

    def test_http_4xx_not_retriable(self):
        assert TransportOutcome.HTTP_4XX not in RETRIABLE_OUTCOMES

    def test_http_5xx_not_retriable(self):
        assert TransportOutcome.HTTP_5XX not in RETRIABLE_OUTCOMES

    def test_request_error_not_retriable(self):
        assert TransportOutcome.REQUEST_ERROR not in RETRIABLE_OUTCOMES

    def test_rate_limit_not_retriable(self):
        assert TransportOutcome.RATE_LIMIT not in RETRIABLE_OUTCOMES

    def test_ok_not_retriable(self):
        assert TransportOutcome.OK not in RETRIABLE_OUTCOMES
