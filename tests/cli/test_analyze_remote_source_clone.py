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


def _write_config(tmp_path: Path, source_url: str, ref: str | None = None) -> Path:
    cfg_path = tmp_path / "nuguard.yaml"
    body = f"source: {source_url}\n"
    if ref is not None:
        body += f"ref: {ref}\n"
    cfg_path.write_text(body, encoding="utf-8")
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
    clone_mock.assert_called_once_with("https://github.com/example/repo", ref=None)
    assert captured["source_path"] == str(cloned_dir)


def test_analyze_clones_configured_ref_instead_of_hardcoded_main(tmp_path: Path) -> None:
    """nuguard.yaml's ref: is forwarded to the clone, not a hardcoded "main"."""
    cfg_path = _write_config(tmp_path, "https://github.com/example/repo", ref="v2")
    cloned_dir = tmp_path / "cloned_repo"
    cloned_dir.mkdir()

    async def fake_run_analysis(request, sbom=None, llm_client=None):  # noqa: ANN001
        from nuguard.analysis.public_api import AnalysisRunResult

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
    clone_mock.assert_called_once_with("https://github.com/example/repo", ref="v2")


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


# ---------------------------------------------------------------------------
# GitHub subfolder dispatch — exercises _clone_remote_source_for_analysis's
# own subfolder gate directly (unmocked), rather than mocking the whole
# function away as the tests above do.
# ---------------------------------------------------------------------------


def test_analyze_clone_routes_tree_url_straight_to_subfolder(tmp_path: Path) -> None:
    from nuguard.cli.commands.analyze import _clone_remote_source_for_analysis

    with (
        patch("nuguard.cli.commands.sbom._resolve_token", return_value=None),
        patch("nuguard.sbom.extractor.core.AiSbomExtractor._clone_repo") as clone_repo_mock,
        patch(
            "nuguard.sbom.extractor.github_clone.clone_github_subfolder"
        ) as clone_subfolder_mock,
    ):
        def _fake_subfolder_clone(repo_root_url, ref, subpath, dest, **kwargs):
            (dest / subpath).mkdir(parents=True, exist_ok=True)
            return "deadbeef"

        clone_subfolder_mock.side_effect = _fake_subfolder_clone

        result = _clone_remote_source_for_analysis(
            "https://github.com/example/repo/tree/main/python-backend"
        )

    clone_repo_mock.assert_not_called()
    clone_subfolder_mock.assert_called_once()
    args, _ = clone_subfolder_mock.call_args
    assert args[0] == "https://github.com/example/repo"
    assert args[2] == "python-backend"
    assert result is not None
    assert result.endswith("python-backend")


def test_analyze_clone_bare_shorthand_falls_back_on_not_found(tmp_path: Path) -> None:
    from nuguard.cli.commands.analyze import _clone_remote_source_for_analysis

    not_found = RuntimeError(
        "git clone failed for 'https://github.com/example/repo/python-backend' @ None: "
        "remote: Not Found\nfatal: repository not found"
    )

    def _fake_clone_repo(*, url, ref, dest):
        raise not_found

    def _fake_subfolder_clone(repo_root_url, ref, subpath, dest, **kwargs):
        (dest / subpath).mkdir(parents=True, exist_ok=True)
        return "deadbeef"

    with (
        patch("nuguard.cli.commands.sbom._resolve_token", return_value=None),
        patch(
            "nuguard.sbom.extractor.core.AiSbomExtractor._clone_repo",
            side_effect=_fake_clone_repo,
        ) as clone_repo_mock,
        patch(
            "nuguard.sbom.extractor.github_clone.clone_github_subfolder",
            side_effect=_fake_subfolder_clone,
        ) as clone_subfolder_mock,
    ):
        result = _clone_remote_source_for_analysis("https://github.com/example/repo/python-backend")

    clone_repo_mock.assert_called_once()
    clone_subfolder_mock.assert_called_once()
    assert result is not None
    assert result.endswith("python-backend")


def test_analyze_clone_bare_shorthand_other_failure_skips_local_scan(tmp_path: Path) -> None:
    """A non-'not found' failure must not be reinterpreted as a subfolder —
    the whole function degrades to None (existing best-effort contract)."""
    from nuguard.cli.commands.analyze import _clone_remote_source_for_analysis

    auth_error = RuntimeError(
        "git clone failed for 'https://github.com/example/repo/python-backend' @ None: "
        "fatal: Authentication failed"
    )

    def _fake_clone_repo(*, url, ref, dest):
        raise auth_error

    with (
        patch("nuguard.cli.commands.sbom._resolve_token", return_value=None),
        patch(
            "nuguard.sbom.extractor.core.AiSbomExtractor._clone_repo",
            side_effect=_fake_clone_repo,
        ),
        patch(
            "nuguard.sbom.extractor.github_clone.clone_github_subfolder"
        ) as clone_subfolder_mock,
    ):
        result = _clone_remote_source_for_analysis("https://github.com/example/repo/python-backend")

    clone_subfolder_mock.assert_not_called()
    assert result is None


def test_analyze_clone_plain_repo_root_url_unaffected(tmp_path: Path) -> None:
    """A plain repo-root URL never touches the new subfolder code path."""
    from nuguard.cli.commands.analyze import _clone_remote_source_for_analysis

    def _fake_clone_repo(*, url, ref, dest):
        dest.mkdir(parents=True, exist_ok=True)

    with (
        patch("nuguard.cli.commands.sbom._resolve_token", return_value=None),
        patch(
            "nuguard.sbom.extractor.core.AiSbomExtractor._clone_repo",
            side_effect=_fake_clone_repo,
        ) as clone_repo_mock,
        patch(
            "nuguard.sbom.extractor.github_clone.clone_github_subfolder"
        ) as clone_subfolder_mock,
    ):
        result = _clone_remote_source_for_analysis("https://github.com/example/repo")

    clone_repo_mock.assert_called_once()
    clone_subfolder_mock.assert_not_called()
    assert result is not None
