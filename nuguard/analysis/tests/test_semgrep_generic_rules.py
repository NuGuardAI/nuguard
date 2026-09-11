"""Semgrep generic-security rule tests for bundled generic-security.yaml.

Runs the ten JS/TS generic-security rules against annotated fixture files.
Skips when semgrep is not installed on PATH (same behaviour as
SemgrepScannerPlugin). In CI, missing semgrep is a hard failure so
rule-behaviour regressions cannot silently skip.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

_RULES = Path(__file__).parent.parent / "plugins" / "semgrep_rules" / "generic-security.yaml"
_FIXTURES = Path(__file__).parent / "fixtures" / "semgrep_generic"


def _semgrep_binary() -> str | None:
    return shutil.which("semgrep")


def _run_semgrep_on_fixtures() -> dict[str, list[dict[str, object]]]:
    """Run bundled rules on all generic fixture files; return findings keyed by filename."""
    binary = _semgrep_binary()
    if binary is None:
        if os.environ.get("CI"):
            pytest.fail(
                "semgrep is required in CI for generic-security rule-behaviour "
                "tests; install semgrep before running pytest"
            )
        pytest.skip("semgrep not installed — generic-security rule tests skipped")

    files = sorted(list(_FIXTURES.glob("*.js")) + list(_FIXTURES.glob("*.jsx")))
    if not files:
        pytest.fail(f"no fixtures found in {_FIXTURES}")

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


class TestGenericSecurityPositives:
    """Each rule fires exactly once on positive.js/.jsx at the expected line."""

    @pytest.mark.parametrize(
        ("filename", "rule_id", "line"),
        [
            ("positive.js", "nuguard-js-sql-injection", 9),
            ("positive.js", "nuguard-js-command-injection", 15),
            ("positive.js", "nuguard-js-path-traversal", 21),
            ("positive.js", "nuguard-js-xss-unescaped-response", 27),
            ("positive.js", "nuguard-js-open-redirect", 31),
            ("positive.js", "nuguard-js-weak-hash-for-password", 35),
            ("positive.js", "nuguard-js-hardcoded-secret", 38),
            ("positive.js", "nuguard-js-insecure-eval", 43),
            ("positive.js", "nuguard-js-insecure-deserialization", 47),
            ("positive.jsx", "nuguard-js-xss-dangerously-set-inner-html", 4),
        ],
    )
    def test_rule_fires_at_expected_line(
        self,
        semgrep_findings: dict[str, list[dict[str, object]]],
        filename: str,
        rule_id: str,
        line: int,
    ) -> None:
        assert _rule_lines(semgrep_findings, filename, rule_id) == [line]


class TestGenericSecurityNegatives:
    """Parameterised/allow-listed/env-sourced equivalents produce no findings."""

    def test_no_findings_on_negative_js(
        self, semgrep_findings: dict[str, list[dict[str, object]]]
    ) -> None:
        assert semgrep_findings.get("negative.js", []) == []
