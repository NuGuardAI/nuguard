"""CLI tests for output-path parent directory auto-creation.

Issue #233: ``nuguard analyze`` and ``nuguard redteam`` failed when the
parent directory of ``--output`` did not exist, while ``nuguard behavior``
silently auto-created it. These tests pin the unified behaviour so the
three commands stay in sync.

Also covers the ``--sbom`` flag added to ``nuguard behavior`` so the SBOM
can be supplied on the CLI instead of only via ``nuguard.yaml``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


_FIXTURE_SBOM = (
    Path(__file__).parent.parent.parent
    / "nuguard"
    / "analysis"
    / "tests"
    / "fixtures"
    / "minimal.sbom.json"
)


# ---------------------------------------------------------------------------
# --output parent-dir auto-creation
# ---------------------------------------------------------------------------


def _ensure_missing(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


@pytest.fixture()
def fresh_tmp(tmp_path: Path) -> Path:
    """Return *tmp_path* with the parent of an ``--output`` target removed."""
    nested = tmp_path / "a" / "b" / "c"
    _ensure_missing(nested)
    return tmp_path


def test_analyze_creates_missing_parent_dir_for_output(fresh_tmp: Path) -> None:
    """``nuguard analyze --output <nested>/report.md`` must auto-create parents."""
    out_path = fresh_tmp / "a" / "b" / "c" / "report.md"
    assert not out_path.parent.exists()
    result = runner.invoke(
        app,
        [
            "analyze",
            "--sbom", str(_FIXTURE_SBOM),
            "--output", str(out_path),
            "--format", "markdown",
            "--no-atlas",
            "--no-osv",
            "--no-supply-chain",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists(), "Expected the report to be written under the missing parent dir"


def test_analyze_creates_missing_parent_dir_for_sarif(fresh_tmp: Path) -> None:
    out_path = fresh_tmp / "a" / "b" / "c" / "report.sarif"
    assert not out_path.parent.exists()
    result = runner.invoke(
        app,
        [
            "analyze",
            "--sbom", str(_FIXTURE_SBOM),
            "--output", str(out_path),
            "--format", "sarif",
            "--no-atlas",
            "--no-osv",
            "--no-supply-chain",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()


def test_behavior_creates_missing_parent_dir_for_output(fresh_tmp: Path) -> None:
    """Behaviour already auto-creates parents — keep that contract under test."""
    out_path = fresh_tmp / "a" / "b" / "c" / "behavior.md"
    result = runner.invoke(
        app,
        [
            "behavior",
            "--sbom", str(_FIXTURE_SBOM),
            "--output", str(out_path),
            "--mode", "static",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()


# ---------------------------------------------------------------------------
# --sbom flag on nuguard behavior
# ---------------------------------------------------------------------------


def test_behavior_accepts_sbom_flag(fresh_tmp: Path) -> None:
    """``nuguard behavior --sbom <path>`` must load the SBOM from the CLI flag.

    The proof is the literal "Loaded SBOM:" log line on stdout — not just a
    successful exit or an output file. The CLI ``--sbom`` flag is the only
    source for this log line; without it the orchestrator's
    ``cfg.sbom_path`` is empty and the SBOM-load step is skipped silently.
    """
    out_path = fresh_tmp / "behavior_sbom_flag.md"
    result = runner.invoke(
        app,
        [
            "behavior",
            "--sbom", str(_FIXTURE_SBOM),
            "--output", str(out_path),
            "--mode", "static",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # Concrete assertion: the CLI flag reached the orchestrator and was used
    # to load the SBOM. Without the forwarding fix, this log line is absent.
    # Rich soft-wraps long paths and may insert a space inside the path
    # (e.g. ``minimal.sbom .json``); we strip inter-word whitespace before
    # substring matching. The assertion still proves that the literal path
    # supplied via ``--sbom`` reached the orchestrator — a different (or
    # missing) path would never produce this string.
    normalised = result.output.replace(" ", "").replace("\n", "")
    assert "LoadedSBOM:" in normalised, (
        f"Expected 'Loaded SBOM:' in output, got:\n{result.output}"
    )
    assert str(_FIXTURE_SBOM).replace(" ", "") in normalised, (
        f"Expected fixture path {str(_FIXTURE_SBOM)!r} in output, got:\n{result.output}"
    )


def test_behavior_sbom_flag_takes_precedence_over_config(tmp_path: Path) -> None:
    """CLI ``--sbom`` should be used when set, ignoring ``sbom:`` in nuguard.yaml.

    The bogus path configured in ``sbom:`` would error loudly if reached
    ("Could not load SBOM from /nonexistent/path.sbom.json" warning + exit
    non-zero with --mode static). Since ``--sbom`` is also supplied, the
    CLI value must win and load the real fixture.
    """
    config = tmp_path / "nuguard.yaml"
    config.write_text(
        "behavior:\n  target: http://localhost:1\nsbom: /nonexistent/path.sbom.json\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "behavior_out.md"
    result = runner.invoke(
        app,
        [
            "behavior",
            "--config", str(config),
            "--sbom", str(_FIXTURE_SBOM),
            "--output", str(out_path),
            "--mode", "static",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # Concrete assertion: the CLI flag won the precedence race, so the load
    # log line shows the fixture path, not the bogus config path. Strip
    # whitespace so Rich's soft-wrap (e.g. inserting a space inside a long
    # path) does not mask a real "Loaded SBOM: <config path>" leak.
    normalised = result.output.replace(" ", "").replace("\n", "")
    assert "LoadedSBOM:" in normalised, (
        f"Expected 'Loaded SBOM:' in output, got:\n{result.output}"
    )
    assert str(_FIXTURE_SBOM).replace(" ", "") in normalised, (
        f"Expected fixture path {str(_FIXTURE_SBOM)!r} in output, got:\n{result.output}"
    )
    assert "/nonexistent/path.sbom.json".replace(" ", "") not in normalised, (
        "Configured bogus sbom: path should have been overridden by --sbom."
    )


# ---------------------------------------------------------------------------
# redteam parent-dir auto-creation (issue #233 parity with analyze/behavior)
# ---------------------------------------------------------------------------


def test_redteam_creates_missing_parent_dir_for_output(fresh_tmp: Path) -> None:
    """``nuguard redteam --output <nested>/report.<fmt>`` must auto-create parents.

    Issues #233 aligns CLI behaviour across analyze / redteam / behavior so
    the three commands all auto-create the parent directory of ``--output``.
    analyze and behavior are covered above; this test pins the redteam
    path. The redteam orchestrator is mocked to return a no-findings
    13-tuple so the test does not depend on a live target or the LLM.
    """
    import json as _json
    from unittest.mock import patch as _patch

    from nuguard.models.token_usage import TokenUsage as _TokenUsage

    out_path = fresh_tmp / "a" / "b" / "c" / "redteam.json"
    assert not out_path.parent.exists()

    async def _fake_run_redteam(*_args, **_kwargs):
        # Empty findings — the test only cares about the parent-dir mkdir,
        # not the redteam content itself.
        return (
            [],     # findings
            [],     # scenario_records
            "no_findings",  # scan_outcome
            [],     # config_notes
            None,   # catalog_coverage
            0,      # input_tokens_used
            0,      # output_tokens_used
            None,   # coverage_tracker
            _TokenUsage(),  # token_usage
            "/chat",  # resolved_chat_path
            "default",  # resolved_chat_path_source
            [],     # remediation_plan
        )

    with _patch("nuguard.cli.commands.redteam._run_redteam", new=_fake_run_redteam):
        result = runner.invoke(
            app,
            [
                "redteam",
                "--sbom", str(_FIXTURE_SBOM),
                "--target", "http://localhost:9999",
                "--output", str(out_path),
                "--format", "json",
            ],
        )

    # exit_code 0 (clean) or 2 (fail-on threshold tripped by any finding)
    # are both acceptable — the contract is that the parent dir is created
    # and the file is written before the threshold check runs.
    assert result.exit_code in (0, 2), result.output
    assert out_path.parent.exists(), (
        f"Expected parent dir {out_path.parent} to be created; stdout:\n{result.output}"
    )
    assert out_path.exists(), (
        f"Expected redteam report at {out_path}; stdout:\n{result.output}"
    )
    # The output file must be valid JSON — proves the dispatch loop ran
    # through the JSON-format branch, not the text-format branch.
    _json.loads(out_path.read_text(encoding="utf-8"))


def test_policy_check_creates_missing_parent_dir_for_output(tmp_path: Path) -> None:
    """``nuguard policy check --output <nested>/report.json`` must auto-create parents.

    Regression: policy check wrote the output file with a bare ``write_text``,
    so a missing parent directory raised an uncaught FileNotFoundError
    traceback, unlike analyze / behavior / redteam / scan / sbom which all
    auto-create the parent (issue #233 unified behaviour).
    """
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.extractor.config import AiSbomConfig
    from nuguard.sbom.extractor.serializer import AiSbomSerializer

    app_dir = (
        Path(__file__).parent.parent.parent
        / "nuguard"
        / "sbom"
        / "tests"
        / "fixtures"
        / "apps"
        / "customer_service_bot"
    )
    doc = AiSbomExtractor().extract_from_path(
        app_dir, AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    )
    sbom = tmp_path / "app.sbom.json"
    sbom.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    pol = tmp_path / "policy.md"
    pol.write_text(
        "## Restricted Topics\n- medical\n## Rate Limits\n- requests_per_minute: 60\n",
        encoding="utf-8",
    )

    out_path = tmp_path / "a" / "b" / "c" / "report.json"
    assert not out_path.parent.exists()
    result = runner.invoke(
        app,
        [
            "policy", "check",
            "--policy", str(pol),
            "--sbom", str(sbom),
            "--output", str(out_path),
            "--format", "json",
        ],
        catch_exceptions=False,
    )
    # exit 0 (no gaps) or 1 (gap findings) are both fine — the contract is
    # that the parent dir is created and the file is written.
    assert result.exit_code in (0, 1), result.output
    assert out_path.exists(), (
        f"Expected policy check report at {out_path}; stdout:\n{result.output}"
    )


def test_policy_compile_creates_missing_parent_dir_for_output(tmp_path: Path) -> None:
    """``nuguard policy compile --output <nested>/controls.json`` must auto-create parents."""
    pol = tmp_path / "policy.md"
    pol.write_text(
        "## Restricted Topics\n- medical\n## Rate Limits\n- requests_per_minute: 60\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "a" / "b" / "c" / "controls.json"
    assert not out_path.parent.exists()
    result = runner.invoke(
        app,
        ["policy", "compile", "--policy", str(pol), "--output", str(out_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists(), (
        f"Expected compiled controls at {out_path}; stdout:\n{result.output}"
    )
