"""Phase 7 — convert confirmed findings into replayable regression tests.

Reuses the shared :func:`nuguard.output.pytest_emitter.emit_regression_tests`
(findings carry their attack payloads in ``attack_steps`` and a severity signal in
``scores``), so v2 findings produce the same CI-runnable regression suite as v1.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.models.finding import Finding

_log = get_logger(__name__)


def emit_regression_suite(
    findings: "list[Finding]",
    target_url: str,
    output_dir: Path | str = Path("./redteam-v2-regression"),
) -> list[Path]:
    """Emit pytest regression tests for confirmed v2 findings.

    Each qualifying finding becomes a test that replays its attack payload and
    asserts the target now refuses it.  Returns the written file paths.
    """
    from nuguard.output.pytest_emitter import emit_regression_tests

    written = emit_regression_tests(findings, target_url=target_url, output_dir=output_dir)
    # Record the regression test id (file stem) back onto each finding's scores so
    # the report can reference it.
    if written:
        _log.info("emitted %d regression file(s) to %s", len(written), output_dir)
    return written
