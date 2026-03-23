"""CLI tests for ``nuguard init``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


def test_init_creates_cognitive_policy_template(tmp_path: Path) -> None:
    out = tmp_path / "cognitive_policy.md"

    result = runner.invoke(app, ["init", "--path", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.read_text(encoding="utf-8") == (
        "# Cognitive Policy\n\n"
        "## Allowed Topics\n\n"
        "## Restricted Topics\n\n"
        "## Restricted Actions\n\n"
        "## HITL Triggers\n\n"
        "## Data Classification\n\n"
        "## Rate Limits\n"
    )


def test_init_does_not_overwrite_without_force(tmp_path: Path) -> None:
    out = tmp_path / "cognitive_policy.md"
    out.write_text("existing\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--path", str(out)])

    assert result.exit_code != 0
    assert out.read_text(encoding="utf-8") == "existing\n"


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    out = tmp_path / "cognitive_policy.md"
    out.write_text("existing\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--path", str(out), "--force"])

    assert result.exit_code == 0, result.output
    assert "## Allowed Topics" in out.read_text(encoding="utf-8")
