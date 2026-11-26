from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


def test_behavior_multi_format_requires_output() -> None:
    result = runner.invoke(app, ["behavior", "--format", "json", "--format", "markdown"])
    assert result.exit_code == 2
    assert "--output is required" in result.output


def test_redteam_multi_format_requires_output() -> None:
    result = runner.invoke(app, ["redteam", "--format", "json", "--format", "markdown"])
    assert result.exit_code == 1
    assert "--output is required" in result.output


def test_redteam_multimodal_rejects_unknown_mode() -> None:
    result = runner.invoke(app, ["redteam", "--multimodal", "bogus"])
    assert result.exit_code == 1
    assert "--multimodal must be one of: auto, on, off" in result.output


def test_redteam_multimodal_accepts_valid_modes_in_any_case() -> None:
    for value in ("auto", "ON", " off "):
        result = runner.invoke(app, ["redteam", "--multimodal", value])
        assert "--multimodal must be one of" not in result.output
