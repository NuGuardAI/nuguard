"""Smoke tests confirming the v2 red-team engine was fully removed (issue #216).

These are intentionally minimal — they exist to catch any future reintroduction
of v2 plumbing (the v2 module, the --engine CLI flag, the v2 config section,
or the v2 Settings class) so that the cleanup stays in place.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


def test_v2_module_path_is_gone() -> None:
    """The package directory must no longer exist on disk."""
    v2_dir = Path(__file__).parent.parent.parent / "nuguard" / "redteam" / "v2"
    assert not v2_dir.exists(), f"v2 directory still present: {v2_dir}"


def test_v2_module_cannot_be_imported() -> None:
    """``nuguard.redteam.v2`` must raise ModuleNotFoundError."""
    import pytest  # noqa: PLC0415

    with pytest.raises(ModuleNotFoundError):
        __import__("nuguard.redteam.v2")


def test_v2_settings_class_is_gone() -> None:
    """``RedteamV2Settings`` must no longer be importable from the config module."""
    import pytest  # noqa: PLC0415

    with pytest.raises(ImportError):
        from nuguard.config import RedteamV2Settings  # noqa: F401


def test_engine_flag_is_removed_from_cli() -> None:
    """The ``--engine`` flag must no longer appear in `nuguard redteam --help` —
    the v2 engine is gone and the flag would only have meant v2."""
    result = runner.invoke(app, ["redteam", "--help"])
    assert result.exit_code == 0, result.output
    assert "--engine" not in result.output


def test_v2_config_keys_are_ignored_silently(tmp_path: Path) -> None:
    """Old nuguard.yaml files with ``redteam.engine: v2`` and ``redteam.v2.*`` must still load."""
    from nuguard.config import load_config  # noqa: PLC0415

    config_file = tmp_path / "nuguard.yaml"
    config_file.write_text(
        "redteam:\n"
        "  engine: v2\n"
        "  v2:\n"
        "    knowledge_base_version: '0.9.0'\n"
        "    max_per_phase: 5\n"
        "    semantic_judge:\n"
        "      count: 4\n"
        "      quorum: 3\n",
        encoding="utf-8",
    )
    # Must not raise
    cfg = load_config(config_file)
    assert cfg is not None