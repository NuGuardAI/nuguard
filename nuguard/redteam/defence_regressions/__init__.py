"""Defence-regression evaluator (redteam-proposal.md W5).

``redteam.defence_regressions`` in ``nuguard.yaml`` configures messages the
target app MUST refuse at all times. Historically this config was parsed
(:data:`nuguard.config.RedteamFindingTriggers`-adjacent
``redteam_defence_regressions``) but never evaluated — a successful attack
against a regression message produced no finding at all, which is dangerous
false confidence in CI: the literal configured string might be refused while
a trivially-similar paraphrase succeeds.

This package makes the configuration load-bearing:

* :mod:`.paraphrase` expands each configured message into N paraphrase
  variants (roleplay, extraction-between-markers, audit-evidence, encoded,
  second-person indirection) using deterministic built-in templates — no
  attack LLM required — or an LLM when one is configured, cached per run.
* :mod:`.evaluator` sends every variant single-turn (no chain, no warmup),
  classifies the response as refused/not-refused via the existing
  :func:`nuguard.redteam.llm_engine.refusal_patterns.is_refusal` heuristic,
  and reports a :class:`~nuguard.redteam.defence_regressions.models.DefenceRegressionResult`
  per variant.
* :func:`nuguard.redteam.defence_regressions.evaluator.build_regression_findings`
  converts failing results (expected "refused" but the attack got through)
  into :class:`~nuguard.models.finding.Finding` objects that are blocking in
  CI regardless of ``output.fail_on`` severity.
"""
from __future__ import annotations

from .evaluator import DefenceRegressionEvaluator, build_regression_findings
from .models import (
    DefenceRegressionResult,
    DefenceRegressionRunSummary,
    DefenceRegressionSpec,
    DefenceRegressionVariant,
)

__all__ = [
    "DefenceRegressionEvaluator",
    "DefenceRegressionResult",
    "DefenceRegressionRunSummary",
    "DefenceRegressionSpec",
    "DefenceRegressionVariant",
    "build_regression_findings",
]
