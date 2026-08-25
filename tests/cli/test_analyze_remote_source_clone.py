"""``nuguard analyze`` auto-clones a remote ``source:`` URL for local-file scans.

Standalone ``nuguard analyze`` (the common ``sbom generate`` then ``analyze``
two-step pipeline used across tests/apps/*) previously silently dropped a
remote ``source:`` URL — the temp clone ``sbom generate`` made is gone by
the time ``analyze`` runs, so supply-chain's raw-file fallback (and
Checkov/Trivy/Semgrep) scanned zero files. ``analyze`` now clones the URL
itself into a temp dir when no local ``--source`` is given.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

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


def _write_config(tmp_path: Path, source_url: str) -> Path:
    cfg_path = tmp_path / "nuguard.yaml"
    cfg_path.write_text(f"source: {source_url}\n", encoding="utf-8")
    return cfg_path


def test_analyze_clones_remote_source_and_passes_it_through(tmp_path: Path) -> None:
    """A remote source: URL is cloned and forwarded as source_path to run_analysis."""
    cfg_path = _write_config(tmp_path, "https://github.com/example/repo")
    cloned_dir = tmp_path / "cloned_repo"
    cloned_dir.mkdir()

    captured: dict[str, object] = {}

    async def fake_run_analysis(request, sbom=None, llm_client=None):  # noqa: ANN001
        from nuguard.analysis.public_api import AnalysisRunResult

        captured["source_path"] = request.source_path
        return AnalysisRunResult(findings=[])

    with (
        patch(
            "nuguard.cli.commands.analyze._clone_remote_source_for_analysis",
            return_value=str(cloned_dir),
        ) as clone_mock,
        patch("nuguard.analysis.public_api.run_analysis", new=AsyncMock(side_effect=fake_run_analysis)),
    ):
        result = runner.invoke(
            app,
            [
                "analyze",
                "--sbom", str(_FIXTURE_SBOM),
                "--config", str(cfg_path),
                "--no-atlas",
                "--no-osv",
                "--no-supply-chain",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    clone_mock.assert_called_once_with("https://github.com/example/repo")
    assert captured["source_path"] == str(cloned_dir)


def test_analyze_falls_back_gracefully_when_clone_fails(tmp_path: Path) -> None:
    """A failed clone degrades to source_path=None instead of crashing analyze."""
    cfg_path = _write_config(tmp_path, "https://github.com/example/repo")

    captured: dict[str, object] = {}

    async def fake_run_analysis(request, sbom=None, llm_client=None):  # noqa: ANN001
        from nuguard.analysis.public_api import AnalysisRunResult

        captured["source_path"] = request.source_path
        return AnalysisRunResult(findings=[])

    with (
        patch(
            "nuguard.cli.commands.analyze._clone_remote_source_for_analysis",
            return_value=None,
        ),
        patch("nuguard.analysis.public_api.run_analysis", new=AsyncMock(side_effect=fake_run_analysis)),
    ):
        result = runner.invoke(
            app,
            [
                "analyze",
                "--sbom", str(_FIXTURE_SBOM),
                "--config", str(cfg_path),
                "--no-atlas",
                "--no-osv",
                "--no-supply-chain",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert captured["source_path"] is None


def test_analyze_does_not_clone_when_local_source_given(tmp_path: Path) -> None:
    """An explicit --source flag (or a local source: path) skips cloning entirely."""
    cfg_path = _write_config(tmp_path, "https://github.com/example/repo")
    local_dir = tmp_path / "local_src"
    local_dir.mkdir()

    with patch(
        "nuguard.cli.commands.analyze._clone_remote_source_for_analysis"
    ) as clone_mock:
        result = runner.invoke(
            app,
            [
                "analyze",
                "--sbom", str(_FIXTURE_SBOM),
                "--config", str(cfg_path),
                "--source", str(local_dir),
                "--no-atlas",
                "--no-osv",
                "--no-supply-chain",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    clone_mock.assert_not_called()
