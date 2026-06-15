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
