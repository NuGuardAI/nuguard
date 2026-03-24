from __future__ import annotations

from tests.run_redteam_validation import (
    BASELINE_MYPY_PATHS,
    ValidationResult,
    _classify_mypy_result,
    _classify_smoke_result,
    _extract_mypy_error_paths,
    _overall_status,
)


def test_extract_mypy_error_paths_parses_unique_paths() -> None:
    output = "\n".join(
        [
            "nuguard/redteam/target/client.py:12: error: bad thing  [assignment]",
            "nuguard/redteam/target/client.py:15: error: bad thing  [assignment]",
            "nuguard/config.py:10: error: another thing  [arg-type]",
        ]
    )

    assert _extract_mypy_error_paths(output) == {
        "nuguard/redteam/target/client.py",
        "nuguard/config.py",
    }


def test_classify_mypy_result_treats_known_baseline_as_warning() -> None:
    baseline_path = sorted(BASELINE_MYPY_PATHS)[0]
    output = f"{baseline_path}:1: error: baseline  [arg-type]"

    status, summary = _classify_mypy_result(1, output)

    assert status == "warning"
    assert "Known baseline mypy errors only" in summary


def test_classify_mypy_result_fails_for_non_baseline_paths() -> None:
    output = "nuguard/redteam/target/client.py:1: error: regression  [assignment]"

    status, summary = _classify_mypy_result(1, output)

    assert status == "failed"
    assert "nuguard/redteam/target/client.py" in summary


def test_classify_smoke_result_warns_on_target_auth_rejection() -> None:
    status, summary = _classify_smoke_result(0, "Target HTTP 401  url=https://example.test/api/v1/new-chat")

    assert status == "warning"
    assert "HTTP 401" in summary


def test_overall_status_prefers_failed_over_warning() -> None:
    results = [
        ValidationResult("a", ["cmd"], "passed", 0, "a.txt", "ok"),
        ValidationResult("b", ["cmd"], "warning", 1, "b.txt", "warn"),
        ValidationResult("c", ["cmd"], "failed", 1, "c.txt", "fail"),
    ]

    assert _overall_status(results) == "failed"