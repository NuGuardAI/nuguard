"""Smoke tests for benchmark helpers under tests/benchmark."""

from __future__ import annotations


def test_evaluate_importable() -> None:
    from tests.benchmark.evaluate import evaluate_repo, list_available_benchmarks
    from tests.benchmark.evaluate_risk import evaluate_repo as evaluate_risk_repo, list_risk_benchmarks

    assert callable(evaluate_repo)
    assert callable(list_available_benchmarks)
    assert callable(evaluate_risk_repo)
    assert callable(list_risk_benchmarks)


def test_fetcher_importable() -> None:
    from tests.benchmark.fetcher import fetch_repo_for_benchmark

    assert callable(fetch_repo_for_benchmark)


def test_fixture_inventory_present() -> None:
    from tests.benchmark.evaluate import list_available_benchmarks
    from tests.benchmark.evaluate_risk import list_risk_benchmarks

    assert "openai-swarm" in list_available_benchmarks()
    assert "Healthcare-voice-agent" in list_risk_benchmarks()
