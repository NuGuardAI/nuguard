"""Bundled AI-security Semgrep rules fire on positive fixtures and stay quiet on negatives.

Each rule here used to be unable to fire at all (an exclusion identical to its
match, or alternatives ANDed under ``patterns:``). Fixtures live in
``tests/analysis/fixtures/semgrep/<rule-id>/{positive,negative}.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RULES = (
    Path(__file__).resolve().parents[2]
    / "nuguard" / "analysis" / "plugins" / "semgrep_rules" / "ai-security.yaml"
)
_FIXTURES = Path(__file__).parent / "fixtures" / "semgrep"

# rule id → matches expected in positive.py (negative.py always expects 0)
_EXPECTED = {
    "nuguard-llm-prompt-injection-fstring": 5,
    "nuguard-eval-on-llm-output": 2,
    "nuguard-hardcoded-api-key": 4,
    "nuguard-llm-ssrf-requests": 3,
    "nuguard-pickle-model-load": 4,
}


def _semgrep() -> str | None:
    local = Path(sys.executable).parent / "semgrep"
    return str(local) if local.exists() else shutil.which("semgrep")


pytestmark = pytest.mark.skipif(_semgrep() is None, reason="semgrep not installed")


def _matches(rule_id: str, fixture: Path) -> int:
    proc = subprocess.run(
        [_semgrep(), "scan", "--config", str(_RULES), "--json", "--quiet",
         "--metrics", "off", "--disable-version-check", str(fixture)],
        capture_output=True, timeout=300, check=False,
    )
    assert proc.returncode in (0, 1), proc.stderr.decode(errors="replace")[-500:]
    results = json.loads(proc.stdout)["results"]
    return sum(1 for r in results if r["check_id"].split(".")[-1] == rule_id)


def test_every_fixture_directory_has_an_expectation() -> None:
    assert sorted(p.name for p in _FIXTURES.iterdir() if p.is_dir()) == sorted(_EXPECTED)


@pytest.mark.parametrize("rule_id", sorted(_EXPECTED))
def test_rule_fires_on_positive_fixture(rule_id: str) -> None:
    assert _matches(rule_id, _FIXTURES / rule_id / "positive.py") == _EXPECTED[rule_id]


@pytest.mark.parametrize("rule_id", sorted(_EXPECTED))
def test_rule_is_quiet_on_negative_fixture(rule_id: str) -> None:
    assert _matches(rule_id, _FIXTURES / rule_id / "negative.py") == 0
