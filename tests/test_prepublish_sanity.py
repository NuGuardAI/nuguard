"""Exercise the real prepublish shell gates with offline report fixtures."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent / "apps" / "prepublish-sanity.sh"
_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    _BASH is None or os.name == "nt", reason="requires a POSIX Bash runtime"
)

# Only the embedded gate Python runs for real. No uv, NuGuard, app or LLM is run.
_FAKE_UV = r"""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] == ["run", "python"]:
    os.execv(sys.executable, [sys.executable, "-S", *args[2:]])
if args[:2] == ["run", "pytest"] or args == ["run", "nuguard", "--help"]:
    raise SystemExit(0)
if args[:4] == ["run", "nuguard", "sbom", "generate"]:
    raise SystemExit(0)
if args[:2] != ["run", "nuguard"] or args[2:3] not in (["behavior"], ["redteam"]):
    raise SystemExit(f"unexpected_fixture_command:{args}")

stage = args[2]
fixture = json.loads(Path(os.environ["SANITY_TEST_FIXTURE"]).read_text())
base = args[args.index("--output") + 1]
if fixture["omit"] != stage:
    path = Path(base + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture["reports"][stage]), encoding="utf-8")
    Path(base + ".md").write_text(stage + " fixture report\n", encoding="utf-8")
raise SystemExit(fixture["rc"])
"""


def _reports() -> dict[str, Any]:
    endpoint = {"target_endpoint_source": "config", "effective_endpoint": "/chat"}
    return {
        "behavior": {
            "scan_outcome": "completed",
            "run_profile": {"scenarios_executed": 2},
            **endpoint,
        },
        "redteam": {
            "scan_outcome": "completed",
            "_meta": endpoint.copy(),
            "diagnostics": {"scenario_traces": [{"status": "completed"}]},
        },
    }


def _run_sanity(
    tmp_path: Path,
    reports: dict[str, Any],
    *,
    rc: int = 0,
    omit: str = "",
    stale: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    assert _BASH is not None
    root = tmp_path / "repo with spaces"
    script = root / "tests" / "apps" / "prepublish-sanity.sh"
    script.parent.mkdir(parents=True)
    script.write_text(_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"reports": reports, "rc": rc, "omit": omit}), encoding="utf-8")
    fake_uv = tmp_path / "fake_uv.py"
    fake_uv.write_text(_FAKE_UV, encoding="utf-8")
    summary = tmp_path / "job-summary.md"
    home = tmp_path / "home"
    home.mkdir()

    if stale:
        # Find the real fixed report paths; do not duplicate the app matrix here.
        entry = next(
            line.strip().strip('"').split("|")
            for line in script.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith('"openai-cs|')
        )
        for stage, base in zip(("behavior", "redteam"), entry[3:5], strict=True):
            path = root / (base + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_reports()[stage]), encoding="utf-8")
            (root / (base + ".md")).write_text("STALE REPORT\n", encoding="utf-8")

    # Exporting a function keeps the stub in use even if the script prepends PATH.
    # A minimal environment prevents inherited credentials or Bash startup hooks.
    result = subprocess.run(
        [
            _BASH,
            "-c",
            'uv() { "$SANITY_TEST_PYTHON" -S "$SANITY_TEST_UV" "$@"; }; '
            'export -f uv; exec "$SANITY_TEST_BASH" "$1" openai-cs',
            "prepublish-fixture",
            str(script),
        ],
        cwd=root,
        env={
            "HOME": str(home),
            "PATH": os.defpath,
            "SANITY_TEST_PYTHON": sys.executable,
            "SANITY_TEST_BASH": _BASH,
            "SANITY_TEST_UV": str(fake_uv),
            "SANITY_TEST_FIXTURE": str(fixture),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return result, summary.read_text(encoding="utf-8") if summary.exists() else ""


def _assert_failed(result: subprocess.CompletedProcess[str], summary: str, reason: str) -> None:
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "[FAIL]" in output, output
    assert "[PASS]" not in output, output
    assert reason in output, output
    assert "**FAILED:**" in summary, summary
    assert reason in summary, summary


@pytest.mark.parametrize("stage", ["behavior", "redteam"])
@pytest.mark.parametrize(
    "outcome",
    [
        "aborted_target_unavailable",
        "inconclusive_target_errors",
        "aborted",
        "aborted:target_unavailable",
        "aborted_interrupted",
        " ABORTED_TARGET_UNAVAILABLE ",
    ],
)
def test_unusable_outcome_fails_despite_nonzero_counts(
    tmp_path: Path, stage: str, outcome: str
) -> None:
    reports = _reports()
    reports[stage]["scan_outcome"] = outcome
    # A bare aborted trace must not conceal a top-level health abort.
    if stage == "redteam":
        reports[stage]["diagnostics"]["scenario_traces"] = [{"status": "aborted"}]
    result, summary = _run_sanity(tmp_path, reports)
    _assert_failed(result, summary, f"{stage}_scan_outcome_not_live:{outcome.strip().lower()}")


@pytest.mark.parametrize("rc", [0, 1, 2])
def test_completed_reports_preserve_allowed_finding_exit_codes(tmp_path: Path, rc: int) -> None:
    result, summary = _run_sanity(tmp_path, _reports(), rc=rc)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "[PASS] openai-cs behavior" in output
    assert "[PASS] openai-cs redteam" in output
    assert "**FAILED:**" not in summary


@pytest.mark.parametrize("stage", ["behavior", "redteam"])
@pytest.mark.parametrize("rc", [1, 2])
def test_allowed_cli_exit_does_not_override_a_health_abort(
    tmp_path: Path, stage: str, rc: int
) -> None:
    reports = _reports()
    reports[stage]["scan_outcome"] = "aborted_target_unavailable"
    result, summary = _run_sanity(tmp_path, reports, rc=rc)
    _assert_failed(result, summary, f"{stage}_scan_outcome_not_live:aborted_target_unavailable")


def test_bare_chain_stop_with_completed_outcome_remains_allowed(tmp_path: Path) -> None:
    reports = _reports()
    reports["redteam"]["diagnostics"]["scenario_traces"] = [{"status": "aborted"}]
    result, _ = _run_sanity(tmp_path, reports)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[PASS] openai-cs redteam" in result.stdout


@pytest.mark.parametrize("stage", ["behavior", "redteam"])
@pytest.mark.parametrize("stale", [False, True])
def test_missing_new_report_cannot_reuse_a_previous_pass(
    tmp_path: Path, stage: str, stale: bool
) -> None:
    result, summary = _run_sanity(tmp_path, _reports(), omit=stage, stale=stale)
    _assert_failed(result, summary, "missing_or_empty_report")
    assert "STALE REPORT" not in summary


@pytest.mark.parametrize("stage", ["behavior", "redteam"])
def test_empty_execution_still_fails(tmp_path: Path, stage: str) -> None:
    reports = _reports()
    if stage == "behavior":
        reports[stage]["run_profile"]["scenarios_executed"] = 0
    else:
        reports[stage]["diagnostics"]["scenario_traces"] = []
    result, summary = _run_sanity(tmp_path, reports)
    _assert_failed(result, summary, f"{stage}_scenarios_executed_zero")


@pytest.mark.parametrize("stage", ["behavior", "redteam"])
@pytest.mark.parametrize("field", ["target_endpoint_source", "effective_endpoint"])
def test_endpoint_mismatch_still_fails(tmp_path: Path, stage: str, field: str) -> None:
    reports = _reports()
    metadata = reports[stage] if stage == "behavior" else reports[stage]["_meta"]
    metadata[field] = "wrong"
    result, summary = _run_sanity(tmp_path, reports)
    _assert_failed(result, summary, f"{stage}_")


@pytest.mark.parametrize(
    "status", ["failed", "skipped", "similar_miss", "aborted:target_unavailable"]
)
def test_all_untested_traces_still_fail(tmp_path: Path, status: str) -> None:
    reports = _reports()
    reports["redteam"]["diagnostics"]["scenario_traces"] = [{"status": status}]
    result, summary = _run_sanity(tmp_path, reports)
    _assert_failed(result, summary, "redteam_all_scenarios_not_tested")
