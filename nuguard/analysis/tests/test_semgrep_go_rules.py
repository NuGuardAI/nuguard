"""Semgrep Go AI-security rule tests for bundled ai-security.yaml.

Runs the four Go rules against annotated fixture files. Skips when semgrep is
not installed on PATH (same behaviour as SemgrepScannerPlugin).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

_RULES = Path(__file__).parent.parent / "plugins" / "semgrep_rules" / "ai-security.yaml"
_FIXTURES = Path(__file__).parent / "fixtures" / "semgrep_go"

_GO_RULE_IDS = frozenset(
    {
        "nuguard-go-llm-prompt-injection-sprintf",
        "nuguard-go-hardcoded-api-key",
        "nuguard-go-llm-missing-context-timeout",
        "nuguard-go-llm-insecure-tls",
    }
)


def _semgrep_binary() -> str | None:
    return shutil.which("semgrep")


def _run_semgrep_on_fixtures() -> dict[str, list[dict[str, object]]]:
    """Run bundled rules on all Go fixture files; return findings keyed by filename."""
    binary = _semgrep_binary()
    if binary is None:
        pytest.skip("semgrep not installed — Go rule tests skipped")

    files = sorted(_FIXTURES.glob("*.go"))
    if not files:
        pytest.fail(f"no Go fixtures found in {_FIXTURES}")

    cmd = [
        binary,
        "--quiet",
        "--json",
        "--config",
        str(_RULES),
        *[str(f) for f in files],
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        pytest.fail(f"semgrep exited {result.returncode}: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    by_file: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in data.get("results") or []:
        path = str(row.get("path", ""))
        filename = Path(path).name
        check_id = str(row.get("check_id", ""))
        rule_id = check_id.rsplit(".", 1)[-1]
        start = row.get("start") or {}
        by_file[filename].append(
            {
                "rule_id": rule_id,
                "line": start.get("line"),
            }
        )
    return by_file


@pytest.fixture(scope="module")
def semgrep_findings() -> dict[str, list[dict[str, object]]]:
    return _run_semgrep_on_fixtures()


def _rule_lines(
    findings: dict[str, list[dict[str, object]]],
    filename: str,
    rule_id: str,
) -> list[int]:
    rows = findings.get(filename, [])
    lines: list[int] = []
    for row in rows:
        if row["rule_id"] != rule_id:
            continue
        line = row.get("line")
        if isinstance(line, int):
            lines.append(line)
    return sorted(lines)


def _assert_no_rule(
    findings: dict[str, list[dict[str, object]]],
    filename: str,
    rule_id: str,
) -> None:
    hits = _rule_lines(findings, filename, rule_id)
    assert hits == [], f"unexpected {rule_id} on {filename}: lines {hits}"


class TestGoPromptInjectionSprintf:
    def test_positive_assignment_and_inline(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "prompt_injection_positive.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )
        assert lines == [13, 23]

    def test_negative_ordinary_sprintf(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "prompt_injection_negative.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )

    def test_negative_static_prompt(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "prompt_injection_negative.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )


class TestGoHardcodedApiKey:
    def test_positive_literals(self, semgrep_findings: dict[str, list[dict[str, object]]]) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "hardcoded_key_positive.go",
            "nuguard-go-hardcoded-api-key",
        )
        assert lines == [8, 14, 20]

    def test_negative_env_and_store(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "hardcoded_key_negative.go",
            "nuguard-go-hardcoded-api-key",
        )


class TestGoMissingContextTimeout:
    def test_positive_background_context(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "missing_timeout_positive.go",
            "nuguard-go-llm-missing-context-timeout",
        )
        assert lines == [10, 22]

    def test_negative_bounded_and_non_llm(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "missing_timeout_negative.go",
            "nuguard-go-llm-missing-context-timeout",
        )


class TestGoInsecureTls:
    def test_positive_llm_client_transport(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "insecure_tls_positive.go",
            "nuguard-go-llm-insecure-tls",
        )
        assert lines == [11, 29]

    def test_negative_secure_and_unrelated(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "insecure_tls_negative.go",
            "nuguard-go-llm-insecure-tls",
        )


class TestGoRuleInventory:
    def test_only_expected_go_rules_fire_on_fixtures(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        fired: set[str] = set()
        for rows in semgrep_findings.values():
            for row in rows:
                rule_id = str(row["rule_id"])
                if rule_id in _GO_RULE_IDS:
                    fired.add(rule_id)
        assert fired == _GO_RULE_IDS
