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
