"""Phase 7 — findings construction and regression export.

* :func:`build_findings` converts confirmed :class:`Verdict` objects into
  :class:`~nuguard.models.finding.Finding` objects with the full v2 field set.
* :func:`emit_regression_suite` emits replayable pytest regression tests.
"""
from __future__ import annotations

from nuguard.redteam.v2.findings.builder import build_finding, build_findings
from nuguard.redteam.v2.findings.regression import emit_regression_suite

__all__ = ["build_finding", "build_findings", "emit_regression_suite"]
