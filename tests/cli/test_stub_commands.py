"""Regression tests for the seed/report/findings/replay stub commands.

Issue #161 — these four commands are placeholders that depend on a
run-history store that has not been built yet. Until that lands they
must:

* exit non-zero (code 3),
* print a clear "not yet implemented" message that references #161,
* point the user at the working alternative (``nuguard redteam
  --output`` / direct re-run).

This file pins all three of those contracts so a future maintainer who
implements one of the commands can remove the corresponding test in
the same change without leaving the others unprotected.
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()

# Rich emits ANSI escape sequences between characters in its styled help
# panels (e.g. ``\x1b[1;36m-\x1b[0m\x1b[1;36m-target\x1b[0m``), which breaks
# a literal substring match on flag tokens like ``--target``. Strip them
# before asserting so the tests pin the semantic help text rather than
# terminal formatting.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


def test_seed_command_is_a_stub() -> None:
    """``nuguard seed --target <url>`` exits non-zero with a #161-aware message.

    Plain ``nuguard seed`` with no args is intercepted by Typer's
    ``no_args_is_help`` and prints help instead — that's expected (the
    stub is reached once the user supplies any actual flag).
    """
    result = runner.invoke(app, ["seed", "--target", "http://example.test"])
    assert result.exit_code == 3
    combined = (result.output + (result.stderr or "")).lower()
    assert "not yet implemented" in combined
    assert "#161" in combined


def test_seed_help_advertises_target_and_canary_options() -> None:
    """Even as a stub, ``nuguard seed --help`` should document its options."""
    result = runner.invoke(app, ["seed", "--help"])
    assert result.exit_code == 0
    # Strip ANSI escape codes before asserting — see _ANSI_RE rationale at
    # the top of this file. Rich's per-character styling inserts ESC
    # sequences between the two dashes of ``--target`` so a literal
    # substring match fails in CI even though the flag is documented.
    plain = _ANSI_RE.sub("", result.output)
    assert "--target" in plain
    assert "--seed-file" in plain
    assert "--output-canary" in plain


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_command_is_a_stub() -> None:
    """``nuguard report`` exits non-zero with a #161-aware message."""
    result = runner.invoke(app, ["report", "--test-id", "abc-123"])
    assert result.exit_code == 3
    combined = (result.output + (result.stderr or "")).lower()
    assert "not yet implemented" in combined
    assert "#161" in combined
    # Should also point at the workaround (--output on redteam).
    assert "--output" in combined or "redteam" in combined


def test_report_requires_test_id() -> None:
    """``nuguard report`` without ``--test-id`` exits with a usage error."""
    result = runner.invoke(app, ["report"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


def test_findings_command_is_a_stub() -> None:
    """``nuguard findings`` exits non-zero with a #161-aware message."""
    result = runner.invoke(app, ["findings", "--test-id", "abc-123"])
    assert result.exit_code == 3
    combined = (result.output + (result.stderr or "")).lower()
    assert "not yet implemented" in combined
    assert "#161" in combined


def test_findings_requires_test_id() -> None:
    """``nuguard findings`` without ``--test-id`` exits with a usage error."""
    result = runner.invoke(app, ["findings"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def test_replay_command_is_a_stub() -> None:
    """``nuguard replay`` exits non-zero with a #161-aware message."""
    result = runner.invoke(app, ["replay", "--test-id", "abc-123"])
    assert result.exit_code == 3
    combined = (result.output + (result.stderr or "")).lower()
    assert "not yet implemented" in combined
    assert "#161" in combined


def test_replay_requires_test_id() -> None:
    """``nuguard replay`` without ``--test-id`` exits with a usage error."""
    result = runner.invoke(app, ["replay"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Top-level visibility — none of the stubs should be silently swallowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["seed", "report", "findings", "replay"],
)
def test_stub_commands_appear_in_top_level_help(command: str) -> None:
    """The four stubs remain advertised in ``nuguard --help`` so users
    discover the planned feature surface (issue #161), even though each
    individual command is a placeholder."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert command in result.output