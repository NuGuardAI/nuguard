"""Basic import and smoke tests for xelo toolbox evaluation utilities."""

from __future__ import annotations


def test_evaluate_importable() -> None:
    """Benchmark evaluation functions are importable."""
    from .evaluate import evaluate_discovery, list_available_benchmarks
    from .evaluate_risk import evaluate_risk_assessment, list_risk_benchmarks

    assert callable(evaluate_discovery)
    assert callable(list_available_benchmarks)
    assert callable(evaluate_risk_assessment)
    assert callable(list_risk_benchmarks)


def test_fetcher_importable() -> None:
    """Fetcher utilities are importable."""
    from .fetcher import fetch_repo_for_benchmark

    assert callable(fetch_repo_for_benchmark)


def test_scan_result_schemas_importable() -> None:
    """Scan + risk result schemas are importable."""
    from .schemas import ScanEvaluationResult
    from .schemas_risk import RiskEvaluationResult

    assert ScanEvaluationResult is not None
    assert RiskEvaluationResult is not None
