"""CLI smoke tests for ``nuguard validate``."""
from __future__ import annotations

from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


def test_validate_requires_target() -> None:
    """nuguard validate exits non-zero and mentions target when no target is configured."""
    result = runner.invoke(app, ["validate"])
    assert result.exit_code != 0, result.output
    assert "target" in result.output.lower()
