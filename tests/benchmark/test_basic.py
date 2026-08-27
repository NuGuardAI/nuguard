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
    from tests.benchmark.evaluate import FIXTURES_DIR, list_available_benchmarks
    from tests.benchmark.evaluate_risk import list_risk_benchmarks

    assert "openai-swarm" in list_available_benchmarks()
    assert "Healthcare-voice-agent" in list_risk_benchmarks()
    # phlox-app/chapterapps/studyield-app were previously hand-curated
    # *.ground-truth.sbom.json files under tests/apps/ that no test ever
    # loaded or diffed (see chapterapp-sbom-fix.md / studyield-sbom-fix.md).
    # Wired into this harness so future adapter changes are measured, not
    # spot-checked. See tests/benchmark/test_wired_ground_truth_fixtures.py
    # for their (currently low, undertuned-ground-truth) baseline scores.
    # chapterapps' fixture is gitignored (real customer secrets), so only
    # assert on repos that are actually materialized locally.
    for repo_name in ("phlox-app", "chapterapps", "studyield-app"):
        if (FIXTURES_DIR / repo_name).exists():
            assert repo_name in list_available_benchmarks()
