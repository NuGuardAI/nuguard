"""Unit tests for nuguard.models.health_report."""
from __future__ import annotations

from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport


def _make_check(
    identity: str = "default",
    status: str = "ok",
    auth_type: str = "bearer",
    http_status_code: int | None = 200,
    response_time_ms: float | None = 42.0,
    error_detail: str = "",
) -> CredentialCheckResult:
    return CredentialCheckResult(
        identity=identity,
        auth_type=auth_type,
        endpoint="http://localhost:3000/chat",
        status=status,  # type: ignore[arg-type]
        http_status_code=http_status_code,
        response_time_ms=response_time_ms,
        error_detail=error_detail,
    )


def _make_report(*checks: CredentialCheckResult) -> TargetHealthReport:
    return TargetHealthReport(
        target_url="http://localhost:3000",
        endpoint="/chat",
        run_id="test-run-001",
        checks=list(checks),
    )


class TestAllOk:
    def test_true_when_all_checks_pass(self) -> None:
        report = _make_report(_make_check(status="ok"), _make_check(identity="t1", status="ok"))
        assert report.all_ok is True

    def test_false_when_one_fails(self) -> None:
        report = _make_report(
            _make_check(status="ok"),
            _make_check(identity="t1", status="auth_failed"),
        )
        assert report.all_ok is False

    def test_true_when_only_skipped_and_ok(self) -> None:
        report = _make_report(
            _make_check(status="ok"),
            _make_check(identity="t1", status="skipped", auth_type="skipped"),
        )
        assert report.all_ok is True

    def test_false_when_target_unavailable(self) -> None:
        report = _make_report(_make_check(status="target_unavailable"))
        assert report.all_ok is False


class TestFailedChecks:
    def test_filters_correctly(self) -> None:
        ok = _make_check(status="ok")
        failed = _make_check(identity="t1", status="auth_failed")
        skipped = _make_check(identity="t2", status="skipped", auth_type="skipped")
        report = _make_report(ok, failed, skipped)
        assert report.failed_checks == [failed]

    def test_empty_when_all_ok(self) -> None:
        report = _make_report(_make_check(status="ok"))
        assert report.failed_checks == []


class TestSummaryLines:
    def test_format_ok(self) -> None:
        report = _make_report(_make_check(status="ok", response_time_ms=123.4))
        lines = report.summary_lines()
        assert len(lines) == 1
        assert "✓" in lines[0]
        assert "default" in lines[0]
        assert "123ms" in lines[0]

    def test_format_auth_failed(self) -> None:
        report = _make_report(
            _make_check(status="auth_failed", error_detail="Unauthorized")
        )
        lines = report.summary_lines()
        assert "✗ AUTH" in lines[0]
        assert "Unauthorized" in lines[0]

    def test_format_skipped(self) -> None:
        report = _make_report(
            _make_check(status="skipped", auth_type="skipped", response_time_ms=None)
        )
        lines = report.summary_lines()
        assert "–" in lines[0]
