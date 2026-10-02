"""Single-turn defence-regression evaluator (W5).

Sends every configured regression message (plus its paraphrase variants)
once each, independently — no chain, no warmup, no multi-turn state — and
classifies the response as refused/not-refused via the same deterministic
heuristic the rest of the redteam engine uses
(:func:`nuguard.redteam.llm_engine.refusal_patterns.is_refusal`). This keeps
the pre-pass cheap (exactly ``len(specs) * (n + 1)`` single-turn requests)
and independent of whether an attack LLM is configured.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.llm_engine.refusal_patterns import is_refusal
from nuguard.redteam.target.session import AttackSession

from .models import (
    DefenceRegressionResult,
    DefenceRegressionRunSummary,
    DefenceRegressionSpec,
    DefenceRegressionVariant,
)
from .paraphrase import generate_variants

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.common.target_client_builder import TargetClient

_log = get_logger(__name__)

_EXCERPT_CHARS = 300


class DefenceRegressionEvaluator:
    """Runs the configured defence-regression specs against a live target."""

    def __init__(self, client: "TargetClient", target_url: str = "") -> None:
        self._client = client
        self._target_url = target_url

    async def run(
        self,
        specs: list[DefenceRegressionSpec],
        n: int,
        llm_client: "LLMClient | None" = None,
    ) -> tuple[list[DefenceRegressionResult], DefenceRegressionRunSummary]:
        """Evaluate every spec's variants and return ``(results, summary)``."""
        results: list[DefenceRegressionResult] = []
        for spec in specs:
            variants = await generate_variants(spec, n, llm_client=llm_client)
            for variant in variants:
                results.append(await self._send_one(spec, variant))
        summary = DefenceRegressionRunSummary(
            specs_evaluated=len(specs),
            variants_evaluated=len(results),
            variants_refused=sum(1 for r in results if r.send_error is None and r.refused),
        )
        _log.info(
            "Defence-regression pre-pass: %d spec(s), %d variant(s), "
            "%.0f%% correctly refused",
            summary.specs_evaluated, summary.variants_evaluated,
            summary.paraphrase_coverage * 100,
        )
        return results, summary

    async def _send_one(
        self, spec: DefenceRegressionSpec, variant: DefenceRegressionVariant,
    ) -> DefenceRegressionResult:
        session = AttackSession(
            session_id=f"defence-regression-{uuid.uuid4().hex[:8]}",
            target_url=self._target_url,
            chain_id="defence-regression",
        )
        try:
            response_text, _ = await self._client.send(
                variant.message, session, retry_transient=True,
            )
        except Exception as exc:  # noqa: BLE001 — one bad send must not abort the pre-pass
            _log.warning(
                "Defence-regression send failed for %r/%s: %s",
                spec.name, variant.variant_kind, exc,
            )
            return DefenceRegressionResult(
                variant=variant, severity=spec.severity, refused=False,
                send_error=str(exc),
            )
        refused = is_refusal(response_text or "")
        excerpt = (response_text or "")[:_EXCERPT_CHARS]
        evidence = (
            f"Regression {spec.name!r} ({variant.variant_kind}) was "
            f"{'refused' if refused else 'NOT refused'}."
        )
        return DefenceRegressionResult(
            variant=variant, severity=spec.severity, refused=refused,
            response_excerpt=excerpt, evidence=evidence,
        )


def build_regression_findings(results: list[DefenceRegressionResult]) -> list[Finding]:
    """Convert hit results (expected refused, got through) into Findings.

    Every finding here must be treated as blocking in CI regardless of
    ``output.fail_on`` — callers check ``finding.regression_paraphrase_kind
    is not None`` rather than severity to decide that (see
    ``redteam.py::_fail_on_severity``).
    """
    findings: list[Finding] = []
    for result in results:
        if not result.is_regression_hit:
            continue
        variant = result.variant
        findings.append(
            Finding(
                finding_id=f"regression-{variant.parent_name}-{variant.variant_kind}-{uuid.uuid4().hex[:8]}",
                title=f"Defence regression: {variant.parent_name} ({variant.variant_kind})",
                severity=Severity(result.severity),
                description=(
                    f"The configured defence regression {variant.parent_name!r} must "
                    f"always be refused, but the {variant.variant_kind} paraphrase "
                    "got through."
                ),
                evidence=result.evidence,
                evidence_quote=result.response_excerpt,
                reasoning=(
                    "Paraphrase of a configured must-refuse message was not refused; "
                    "see redteam.defence_regressions in nuguard.yaml."
                ),
                regression_paraphrase_kind=variant.variant_kind,
                authorization_decision="allow",
                guardrail_control="none",
            )
        )
    return findings
