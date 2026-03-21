"""Benchmark policy fixture tests."""

from __future__ import annotations

from pathlib import Path

from nuguard.policy.parser import parse_policy
from nuguard.policy.validator import lint_policy


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_benchmark_policy_examples_parse_and_lint() -> None:
    for repo_name in ("openai-cs-agents-demo", "Healthcare-voice-agent"):
        policy_path = FIXTURES_DIR / repo_name / "cognitive_policy.md"
        assert policy_path.exists(), f"Missing benchmark policy fixture: {policy_path}"

        policy = parse_policy(policy_path.read_text(encoding="utf-8"))
        issues = lint_policy(policy)
        errors = [issue for issue in issues if issue.severity == "error"]

        assert policy.allowed_topics
        assert policy.restricted_actions
        assert policy.hitl_triggers
        assert not errors, f"Unexpected policy lint errors for {repo_name}: {errors}"
