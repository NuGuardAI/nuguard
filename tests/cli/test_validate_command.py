"""CLI smoke tests for ``nuguard validate`` Phase 3 stub."""
from __future__ import annotations

from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


def test_validate_stub_exits_0() -> None:
    """nuguard validate prints the Phase 3 stub message and exits 0."""
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 0, result.output
    assert "Phase 3" in result.output


def test_validate_stub_mentions_target_verify() -> None:
    """nuguard validate directs users to nuguard target verify."""
    result = runner.invoke(app, ["validate"])
    assert "target verify" in result.output
