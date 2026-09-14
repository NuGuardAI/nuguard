"""Regression tests for parity with legacy endpoint helpers."""

from nuguard.common.endpoint_detection import normalize_probe_result
from nuguard.common.endpoint_detection.rotation import response_indicates_wrong_endpoint
from nuguard.common.endpoint_probe import ProbeResult


def test_probe_normalizer_preserves_legacy_result_identity() -> None:
    result = ProbeResult("/api/chat", "message", False)

    assert normalize_probe_result(result) is result


def test_empty_response_is_wrong_even_with_success_status() -> None:
    assert response_indicates_wrong_endpoint("", status_code=200) is True
    assert response_indicates_wrong_endpoint("  ", status_code=200) is True


def test_non_empty_success_response_is_not_wrong() -> None:
    assert response_indicates_wrong_endpoint('{"answer":"ok"}', status_code=200) is False