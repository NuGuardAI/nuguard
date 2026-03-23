"""Tests for orchestrator outcome-state computation and transport health tallying.

Covers:
- _classify_step_transport() for all response patterns
- _tally_transport() accumulation into ScenarioRecord
- _compute_scan_outcome() for all four outcome values and strict/non-strict modes
- Backward-compatibility: no-header / non-strict runs behave exactly as before
"""
from __future__ import annotations

import pytest

from nuguard.redteam.executor.orchestrator import (
    ScenarioRecord,
    _classify_step_transport,
    _compute_scan_outcome,
    _tally_transport,
)


# ── _classify_step_transport ──────────────────────────────────────────────────


def test_classify_2xx_from_status_code():
    assert _classify_step_transport("ok", 200) == "http_2xx"


def test_classify_4xx_from_status_code():
    assert _classify_step_transport("[HTTP 401]", 401) == "http_4xx"


def test_classify_4xx_404_from_status_code():
    assert _classify_step_transport("[HTTP 404]", 404) == "http_4xx"


def test_classify_5xx_from_status_code():
    assert _classify_step_transport("[HTTP 500]", 500) == "http_5xx"


def test_classify_504_from_status_code():
    assert _classify_step_transport("[HTTP 504]", 504) == "http_5xx"


def test_classify_2xx_from_response_string():
    """Chat send() success has no error prefix and no status code."""
    assert _classify_step_transport("Hello, how can I help?", None) == "http_2xx"


def test_classify_4xx_from_response_string():
    assert _classify_step_transport("[HTTP 401]", None) == "http_4xx"


def test_classify_5xx_from_response_string():
    assert _classify_step_transport("[HTTP 503]", None) == "http_5xx"


def test_classify_request_error_from_response_string():
    assert _classify_step_transport("[REQUEST_ERROR: ConnectError: refused]", None) == "request_error"


def test_classify_timeout_from_response_string():
    assert _classify_step_transport("[REQUEST_ERROR: TimeoutException: timeout]", None) == "timeout_error"


def test_classify_timeout_case_insensitive():
    assert _classify_step_transport("[REQUEST_ERROR: ReadTimeout: timed out]", None) == "timeout_error"


def test_status_code_takes_precedence_over_response_string():
    """If http_status_code is set it is used regardless of response text."""
    assert _classify_step_transport("[REQUEST_ERROR: something]", 422) == "http_4xx"


# ── _tally_transport ──────────────────────────────────────────────────────────


class _FakeStepResult:
    def __init__(self, response: str, http_status_code: int | None = None) -> None:
        self.response = response
        self.http_status_code = http_status_code


def _make_record() -> ScenarioRecord:
    return ScenarioRecord(
        title="test",
        goal_type="PROMPT_DRIVEN_THREAT",
        scenario_type="prompt_injection",
        description="test scenario",
        impact_score=7.0,
        affected="Agent (AGENT)",
        chain_status="completed",
        had_finding=False,
    )


def test_tally_all_2xx():
    record = _make_record()
    results = [_FakeStepResult("response text") for _ in range(3)]
    _tally_transport(record, results)
    assert record.http_2xx == 3
    assert record.http_4xx == 0
    assert record.http_5xx == 0


def test_tally_mixed():
    record = _make_record()
    results = [
        _FakeStepResult("ok", 200),
        _FakeStepResult("[HTTP 401]", 401),
        _FakeStepResult("[HTTP 500]", 500),
        _FakeStepResult("[REQUEST_ERROR: ConnectError]", None),
        _FakeStepResult("[REQUEST_ERROR: ReadTimeout]", None),
    ]
    _tally_transport(record, results)
    assert record.http_2xx == 1
    assert record.http_4xx == 1
    assert record.http_5xx == 1
    assert record.request_errors == 1
    assert record.timeout_errors == 1


def test_tally_all_504():
    record = _make_record()
    results = [_FakeStepResult("[HTTP 504]", 504) for _ in range(4)]
    _tally_transport(record, results)
    assert record.http_5xx == 4
    assert record.http_2xx == 0


# ── _compute_scan_outcome ─────────────────────────────────────────────────────


def _record_with_counters(
    chain_status: str = "completed",
    http_2xx: int = 0,
    http_4xx: int = 0,
    http_5xx: int = 0,
    request_errors: int = 0,
    timeout_errors: int = 0,
) -> ScenarioRecord:
    r = _make_record()
    r.chain_status = chain_status
    r.http_2xx = http_2xx
    r.http_4xx = http_4xx
    r.http_5xx = http_5xx
    r.request_errors = request_errors
    r.timeout_errors = timeout_errors
    return r


def _fake_finding() -> object:
    """Return a truthy stand-in for a Finding object."""
    return object()


def test_outcome_passed_when_findings():
    """Any finding → passed, regardless of transport health."""
    records = [_record_with_counters(http_5xx=5)]
    outcome = _compute_scan_outcome(findings=[_fake_finding()], records=records, strict=True)
    assert outcome == "passed"


def test_outcome_no_findings_default():
    records = [_record_with_counters(http_2xx=5)]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=False)
    assert outcome == "no_findings"


def test_outcome_no_findings_when_all_4xx_strict():
    """All-4xx transport (401) → no_findings even in strict mode (target was alive)."""
    records = [_record_with_counters(http_4xx=5)]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=True)
    assert outcome == "no_findings"


def test_outcome_aborted_target_unavailable():
    """All scenarios aborted → aborted_target_unavailable."""
    records = [
        _record_with_counters(chain_status="aborted"),
        _record_with_counters(chain_status="aborted"),
    ]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=False)
    assert outcome == "aborted_target_unavailable"


def test_outcome_aborted_target_unavailable_all_skipped():
    """All scenarios skipped (circuit already open) → aborted_target_unavailable."""
    records = [
        _record_with_counters(chain_status="skipped"),
        _record_with_counters(chain_status="skipped"),
    ]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=False)
    assert outcome == "aborted_target_unavailable"


def test_outcome_inconclusive_strict_all_504():
    """strict=True + ≥80 % server errors → inconclusive_target_errors."""
    records = [_record_with_counters(http_5xx=5, http_2xx=1)]  # 5/6 ≈ 83 %
    outcome = _compute_scan_outcome(findings=[], records=records, strict=True)
    assert outcome == "inconclusive_target_errors"


def test_outcome_not_inconclusive_when_not_strict():
    """strict=False with all 5xx → still no_findings (legacy behaviour)."""
    records = [_record_with_counters(http_5xx=5)]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=False)
    assert outcome == "no_findings"


def test_outcome_not_inconclusive_below_threshold():
    """strict=True but < 80 % server errors → no_findings."""
    records = [_record_with_counters(http_5xx=2, http_2xx=5)]  # 2/7 ≈ 29 %
    outcome = _compute_scan_outcome(findings=[], records=records, strict=True)
    assert outcome == "no_findings"


def test_outcome_inconclusive_from_request_errors():
    """Request errors (network failures) count toward inconclusive threshold."""
    records = [_record_with_counters(request_errors=4, http_2xx=1)]  # 4/5 = 80 %
    outcome = _compute_scan_outcome(findings=[], records=records, strict=True)
    assert outcome == "inconclusive_target_errors"


def test_outcome_inconclusive_from_timeout_errors():
    """Timeout errors count toward inconclusive threshold."""
    records = [_record_with_counters(timeout_errors=4, http_2xx=1)]  # 4/5 = 80 %
    outcome = _compute_scan_outcome(findings=[], records=records, strict=True)
    assert outcome == "inconclusive_target_errors"


def test_outcome_exactly_at_threshold():
    """80 % exactly triggers inconclusive."""
    # 4 errors + 1 success = 80 %
    records = [_record_with_counters(http_5xx=4, http_2xx=1)]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=True)
    assert outcome == "inconclusive_target_errors"


def test_outcome_just_below_threshold():
    """79 % does NOT trigger inconclusive."""
    # 79 errors + 21 successes = 79 %
    records = [_record_with_counters(http_5xx=79, http_2xx=21)]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=True)
    assert outcome == "no_findings"


def test_outcome_empty_records():
    """No scenario records → no_findings (edge case: nothing ran)."""
    outcome = _compute_scan_outcome(findings=[], records=[], strict=True)
    assert outcome == "no_findings"


def test_outcome_mixed_aborted_and_completed():
    """Mixed aborted + completed → not flagged as aborted_target_unavailable."""
    records = [
        _record_with_counters(chain_status="aborted"),
        _record_with_counters(chain_status="completed", http_2xx=3),
    ]
    outcome = _compute_scan_outcome(findings=[], records=records, strict=False)
    assert outcome == "no_findings"
