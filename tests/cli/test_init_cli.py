"""CLI tests for ``nuguard init``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


def test_init_creates_starter_files(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    cognitive_policy = tmp_path / "cognitive-policy.md"
    canary = tmp_path / "canary.example.json"
    config = tmp_path / "nuguard.yaml"

    assert cognitive_policy.exists()
    assert canary.exists()
    assert config.exists()

    assert cognitive_policy.read_text(encoding="utf-8") == (
        "# Cognitive Policy\n\n"
        "## Allowed Topics\n\n"
        "## Restricted Topics\n\n"
        "## Restricted Actions\n\n"
        "## HITL Triggers\n\n"
        "## Data Classification\n\n"
        "## Rate Limits\n"
    )


def test_init_does_not_overwrite_without_force(tmp_path: Path) -> None:
    cognitive_policy = tmp_path / "cognitive-policy.md"
    cognitive_policy.parent.mkdir(parents=True, exist_ok=True)
    cognitive_policy.write_text("existing\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert cognitive_policy.read_text(encoding="utf-8") == "existing\n"
    assert "skipped" in result.output


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    cognitive_policy = tmp_path / "cognitive-policy.md"
    cognitive_policy.parent.mkdir(parents=True, exist_ok=True)
    cognitive_policy.write_text("existing\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--dir", str(tmp_path), "--force"])

    assert result.exit_code == 0, result.output
    assert "## Allowed Topics" in cognitive_policy.read_text(encoding="utf-8")
