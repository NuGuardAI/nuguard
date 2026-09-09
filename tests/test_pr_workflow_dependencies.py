"""Regression tests for pull-request workflow dependencies."""

from pathlib import Path

import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_PR_WORKFLOW = _REPOSITORY_ROOT / ".github" / "workflows" / "pr-tests.yml"
_REQUIRED_TEST_DEPENDENCIES = frozenset(
    {
        "lint",
        "public-api-contract",
        "browser_login",
    }
)


def test_full_test_job_preserves_required_pr_gates() -> None:
    workflow = yaml.safe_load(_PR_WORKFLOW.read_text(encoding="utf-8"))

    assert isinstance(workflow, dict)

    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)

    missing_jobs = _REQUIRED_TEST_DEPENDENCIES - set(jobs)
    assert not missing_jobs, f"Required PR workflow jobs are missing: {sorted(missing_jobs)}"

    test_job = jobs.get("test")
    assert isinstance(test_job, dict)

    raw_needs = test_job.get("needs")

    if isinstance(raw_needs, str):
        dependencies = {raw_needs}
    else:
        assert isinstance(raw_needs, list)
        assert all(isinstance(dependency, str) for dependency in raw_needs)
        dependencies = set(raw_needs)

    missing_dependencies = _REQUIRED_TEST_DEPENDENCIES - dependencies

    assert not missing_dependencies, (
        f"The full test job dropped required PR gate(s): {sorted(missing_dependencies)}"
    )
