"""Semgrep C# AI-security rule tests for bundled ai-security.yaml.

Runs the C# rules against positive and negative fixtures. Semgrep is optional
for local development, but CI must provide the pinned executable so rule
regressions cannot silently skip.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

from nuguard.analysis.plugins.semgrep_scanner import (
    SemgrepScannerPlugin,
)

_RULES = Path(__file__).parent.parent / "plugins" / "semgrep_rules" / "ai-security.yaml"
_FIXTURES = Path(__file__).parent / "fixtures" / "semgrep_csharp"

_CSHARP_RULE_IDS = frozenset(
    {
        ("nuguard-csharp-llm-prompt-injection-interpolation"),
        "nuguard-csharp-llm-unsanitized-input",
        "nuguard-csharp-llm-unvalidated-output",
    }
)

_EXPECTED_POSITIVE = {
    "prompt_interpolation_positive.cs": ("nuguard-csharp-llm-prompt-injection-interpolation"),
    "unsanitized_input_positive.cs": ("nuguard-csharp-llm-unsanitized-input"),
    "unvalidated_output_positive.cs": ("nuguard-csharp-llm-unvalidated-output"),
}

_NEGATIVE_FIXTURES = (
    "prompt_interpolation_negative.cs",
    "unsanitized_input_negative.cs",
    "unvalidated_output_negative.cs",
)


def _semgrep_binary() -> str | None:
    return shutil.which("semgrep")


def _require_semgrep() -> str:
    binary = _semgrep_binary()

    if binary is not None:
        return binary

    if os.environ.get("CI"):
        pytest.fail(
            "semgrep is required in CI for C# "
            "rule-behaviour tests; install semgrep "
            "before running pytest"
        )

    pytest.skip("semgrep not installed — C# rule tests skipped")


def _normalize_rule_id(
    check_id: str,
) -> str:
    return check_id.rsplit(".", 1)[-1]


def _run_semgrep_on_fixtures() -> dict[str, list[dict[str, object]]]:
    binary = _require_semgrep()
    files = sorted(_FIXTURES.glob("*.cs"))

    if not files:
        pytest.fail(f"no C# fixtures found in {_FIXTURES}")

    cmd = [
        binary,
        "--quiet",
        "--json",
        "--config",
        str(_RULES),
        *[str(path) for path in files],
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode not in (0, 1):
        pytest.fail(f"semgrep exited {result.returncode}: {result.stderr.strip()}")

    data = json.loads(result.stdout)

    by_file: dict[
        str,
        list[dict[str, object]],
    ] = defaultdict(list)

    for row in data.get("results") or []:
        result_path = str(row.get("path", ""))
        start = row.get("start") or {}

        by_file[Path(result_path).name].append(
            {
                "rule_id": _normalize_rule_id(
                    str(
                        row.get(
                            "check_id",
                            "",
                        )
                    )
                ),
                "line": start.get("line"),
            }
        )

    return by_file


@pytest.fixture(scope="module")
def semgrep_findings() -> dict[str, list[dict[str, object]]]:
    return _run_semgrep_on_fixtures()


def _rule_ids(
    findings: dict[
        str,
        list[dict[str, object]],
    ],
    filename: str,
) -> set[str]:
    return {
        str(row["rule_id"])
        for row in findings.get(
            filename,
            [],
        )
        if str(row["rule_id"]) in _CSHARP_RULE_IDS
    }


def _rule_lines(
    findings: dict[str, list[dict[str, object]]],
    filename: str,
    rule_id: str,
) -> list[int]:
    lines: list[int] = []

    for row in findings.get(filename, []):
        if row.get("rule_id") != rule_id:
            continue

        line = row.get("line")

        if isinstance(line, int):
            lines.append(line)

    return sorted(lines)


@pytest.mark.parametrize(
    (
        "filename",
        "expected_rule",
    ),
    sorted(_EXPECTED_POSITIVE.items()),
)
def test_positive_fixture_emits_expected_rule(
    semgrep_findings: dict[
        str,
        list[dict[str, object]],
    ],
    filename: str,
    expected_rule: str,
) -> None:
    lines = _rule_lines(
        semgrep_findings,
        filename,
        expected_rule,
    )

    assert lines, (
        f"{filename}: expected {expected_rule}; "
        "fired="
        f"{sorted(_rule_ids(semgrep_findings, filename))}"
    )


@pytest.mark.parametrize(
    "filename",
    _NEGATIVE_FIXTURES,
)
def test_sanitized_fixture_has_no_csharp_findings(
    semgrep_findings: dict[
        str,
        list[dict[str, object]],
    ],
    filename: str,
) -> None:
    assert (
        _rule_ids(
            semgrep_findings,
            filename,
        )
        == set()
    )


def test_fixture_pack_covers_all_csharp_rules(
    semgrep_findings: dict[
        str,
        list[dict[str, object]],
    ],
) -> None:
    fired: set[str] = set()

    for rows in semgrep_findings.values():
        fired.update(str(row["rule_id"]) for row in rows if str(row["rule_id"]) in _CSHARP_RULE_IDS)

    assert fired == _CSHARP_RULE_IDS


def test_semgrep_plugin_reports_csharp_rule_metadata(
    tmp_path: Path,
) -> None:
    _require_semgrep()

    source_dir = tmp_path / "csharp-source"
    source_dir.mkdir()

    for fixture in sorted(_FIXTURES.glob("*.cs")):
        shutil.copy2(
            fixture,
            source_dir / fixture.name,
        )

    result = SemgrepScannerPlugin().run(
        {"nodes": []},
        {
            "source_path": str(source_dir),
            "semgrep_exclude_tests": False,
            "semgrep_timeout": 120.0,
        },
    )

    assert result.status == "warning"
    assert result.plugin == "semgrep"

    findings = [
        finding
        for finding in result.findings
        if _normalize_rule_id(
            str(
                finding.get(
                    "rule_id",
                    "",
                )
            )
        )
        in _CSHARP_RULE_IDS
    ]

    fired = {
        _normalize_rule_id(
            str(
                finding.get(
                    "rule_id",
                    "",
                )
            )
        )
        for finding in findings
    }

    assert fired == _CSHARP_RULE_IDS

    assert all(finding.get("source") == "semgrep" for finding in findings)

    assert all(finding.get("nga_rule") == "NGA-002" for finding in findings)

    assert all(
        str(
            finding.get(
                "owasp_llm_ref",
                "",
            )
        ).startswith("LLM0")
        for finding in findings
    )

    assert all(
        any(
            ".cs:" in str(location)
            for location in (
                finding.get(
                    "affected",
                    [],
                )
                or []
            )
        )
        for finding in findings
    )
