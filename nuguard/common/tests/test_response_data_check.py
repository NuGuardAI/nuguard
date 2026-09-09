"""Tests for HTTP-status-aware data-exposure detection (redteam signal Gap D).

A FastAPI/Pydantic validation-error envelope, or a plain 404 body, naturally
satisfies the "bulk record list" heuristic (a list of >1 dicts) without
carrying any real data. These tests confirm 4xx/5xx bodies are no longer
flagged on that heuristic alone, while a genuine leak (PII values, matched
sensitive field names, or a 2xx bulk record list) still triggers.
"""
import json

from nuguard.common.response_data_check import check_response_for_data_exposure


def test_pydantic_validation_error_envelope_not_flagged_as_exposure():
    body = json.dumps(
        {
            "detail": [
                {"loc": ["body", "name"], "msg": "field required", "type": "value_error.missing"},
                {"loc": ["body", "pdf"], "msg": "field required", "type": "value_error.missing"},
            ]
        }
    )
    result = check_response_for_data_exposure(body, http_status_code=422)
    assert not result.exposed


def test_404_not_found_body_not_flagged():
    body = json.dumps({"detail": "No stored PDF found for id abc-123"})
    result = check_response_for_data_exposure(body, http_status_code=404)
    assert not result.exposed


def test_2xx_bulk_record_list_still_flagged():
    body = json.dumps([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    result = check_response_for_data_exposure(body, http_status_code=200)
    assert result.exposed
    assert result.record_count == 2


def test_4xx_body_with_real_pii_still_flagged():
    body = json.dumps(
        {
            "detail": [
                {"loc": ["body"], "msg": "duplicate email john.doe@example.com already exists"},
                {"loc": ["body"], "msg": "SSN 123-45-6789 already on file"},
            ]
        }
    )
    result = check_response_for_data_exposure(body, http_status_code=422)
    assert result.exposed
    assert result.pii_values


def test_4xx_bulk_list_without_status_code_still_uses_legacy_behavior():
    # No http_status_code supplied (unknown) — preserve prior behavior rather
    # than guessing.
    body = json.dumps([{"a": 1}, {"b": 2}])
    result = check_response_for_data_exposure(body)
    assert result.exposed
