"""Semgrep Go AI-security rule tests for bundled ai-security.yaml.

Runs the four Go rules against annotated fixture files. Skips when semgrep is
not installed on PATH (same behaviour as SemgrepScannerPlugin). In CI, missing
semgrep is a hard failure so rule-behaviour regressions cannot silently skip.
"""

from __future__ import annotations

import json
import os
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
        if os.environ.get("CI"):
            pytest.fail(
                "semgrep is required in CI for Go rule-behaviour tests; "
                "install semgrep before running pytest"
            )
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
    def test_case1_untrusted_through_sprintf_to_llm(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        """user/external/dynamic input -> fmt.Sprintf -> LLM = MATCH."""
        lines = _rule_lines(
            semgrep_findings,
            "prompt_injection_positive.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )
        assert lines == [13, 23]

    def test_case2_trusted_literal_sprintf_to_llm(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        """trusted literal/constant -> fmt.Sprintf -> LLM = NO MATCH."""
        _assert_no_rule(
            semgrep_findings,
            "prompt_injection_negative.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )

    def test_case3_trusted_app_config_sprintf_to_llm(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        """trusted application-configuration value -> fmt.Sprintf -> LLM = NO MATCH."""
        _assert_no_rule(
            semgrep_findings,
            "prompt_injection_negative.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )

    def test_case4_sprintf_without_llm_sink(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        """user/dynamic input -> fmt.Sprintf but no LLM sink = NO MATCH."""
        _assert_no_rule(
            semgrep_findings,
            "prompt_injection_negative.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )

    def test_case5_direct_string_param_to_llm_without_sprintf(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        """string parameter -> LLM directly, with no fmt.Sprintf = NO MATCH."""
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
        assert lines == [16, 22, 28, 34]

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
        assert lines == [27, 45, 63]

    def test_negative_secure_and_unrelated(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "insecure_tls_negative.go",
            "nuguard-go-llm-insecure-tls",
        )


class TestGoOfficialSdkSinks:
    """Issue #223: qualified official SDK sinks (never bare New)."""

    def test_official_positive_prompt_injection(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "official_sdk_positive.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )
        # Chat.Completions.New, Messages.New, Converse(...optFn), GenerateContent
        assert lines == [54, 65, 84, 90]

    def test_official_positive_missing_timeout(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "official_sdk_positive.go",
            "nuguard-go-llm-missing-context-timeout",
        )
        # Includes Bedrock InvokeModel/Converse calls that pass a trailing optFn.
        assert lines == [54, 59, 65, 70, 77, 84, 90]

    def test_bare_new_is_not_llm_sink(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        for rule_id in _GO_RULE_IDS:
            _assert_no_rule(semgrep_findings, "official_sdk_negative.go", rule_id)


class TestGoOfficialStreamingSinks:
    """Issue #232: official SDK streaming call shapes."""

    def test_streaming_prompt_injection(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "official_sdk_streaming_positive.go",
            "nuguard-go-llm-prompt-injection-sprintf",
        )
        # Chat.Completions.NewStreaming and Models.GenerateContentStream.
        assert lines == [63, 91]

    def test_streaming_missing_timeout(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "official_sdk_streaming_positive.go",
            "nuguard-go-llm-missing-context-timeout",
        )
        # NewStreaming x3, ConverseStream, InvokeModelWithResponseStream,
        # GenerateContentStream.
        assert lines == [63, 71, 77, 83, 91]

    def test_bounded_context_streaming_not_reported(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        """A timeout-bounded streaming call with trusted input stays clean."""
        for rule_id in (
            "nuguard-go-llm-prompt-injection-sprintf",
            "nuguard-go-llm-missing-context-timeout",
        ):
            hits = _rule_lines(semgrep_findings, "official_sdk_streaming_positive.go", rule_id)
            assert 99 not in hits


class TestGoCredentialOptions:
    """Issue #232: hardcoded credentials via official client options."""

    def test_positive_literal_options(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "credential_options_positive.go",
            "nuguard-go-hardcoded-api-key",
        )
        # WithAPIKey("sk-…"), WithAuthToken("sk-…"), WithToken("AIza…").
        assert lines == [15, 24, 29]

    def test_negative_env_and_non_secret(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "credential_options_negative.go",
            "nuguard-go-hardcoded-api-key",
        )


class TestGoInsecureTLSViaHTTPClientOption:
    """Issue #232: InsecureSkipVerify wired through option.WithHTTPClient."""

    def test_positive_variable_and_literal_transport(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        lines = _rule_lines(
            semgrep_findings,
            "http_client_option_positive.go",
            "nuguard-go-llm-insecure-tls",
        )
        assert lines == [25, 40]

    def test_negative_secure_and_non_ai(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        _assert_no_rule(
            semgrep_findings,
            "http_client_option_negative.go",
            "nuguard-go-llm-insecure-tls",
        )


class TestGoRuleInventory:
    def test_only_expected_go_rules_fire_on_fixtures(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        fired: set[str] = set()
        for rows in semgrep_findings.values():
            for row in rows:
                fired.add(str(row["rule_id"]))
        assert fired == _GO_RULE_IDS
