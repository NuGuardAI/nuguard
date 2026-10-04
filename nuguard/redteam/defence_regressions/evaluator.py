"""Single-turn defence-regression evaluator (W5).

Sends every configured regression message (plus its paraphrase variants)
once each, independently — no chain, no warmup, no multi-turn state — and
classifies the response as refused/not-refused via the same deterministic
heuristic the rest of the redteam engine uses
(:func:`nuguard.redteam.llm_engine.refusal_patterns.is_refusal`). This keeps
the pre-pass bounded by wall-clock deadlines, with single-turn probes and
short transport retries. Built-in paraphrases work without an attack LLM.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger
from nuguard.common.transport import TransportOutcome, classify_transport, error_only_response
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
        *,
        timeout_seconds: float = 180.0,
        probe_timeout_seconds: float = 30.0,
    ) -> tuple[list[DefenceRegressionResult], DefenceRegressionRunSummary]:
        """Evaluate every spec's variants and return ``(results, summary)``."""
        if timeout_seconds <= 0 or probe_timeout_seconds <= 0:
            raise ValueError("Defence-regression deadlines must be positive")
        results: list[DefenceRegressionResult] = []
        started = time.monotonic()
        evaluated_specs = 0
        timed_out = False
        _log.info(
            "Defence-regression pre-pass start: specs=%d paraphrases=%d timeout=%.1fs probe_timeout=%.1fs",
            len(specs),
            n,
            timeout_seconds,
            probe_timeout_seconds,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                for spec in specs:
                    _log.info("Defence-regression variants start: name=%r", spec.name)
                    variants = await generate_variants(spec, n, llm_client=llm_client)
                    evaluated_specs += 1
                    for index, variant in enumerate(variants, 1):
                        probe_start = time.monotonic()
                        _log.info(
                            "Defence-regression probe start: name=%r variant=%s generated_by=%s index=%d/%d",
                            spec.name,
                            variant.variant_kind,
                            variant.generated_by,
                            index,
                            len(variants),
                        )
                        result = await self._send_one(spec, variant, probe_timeout_seconds)
                        results.append(result)
                        _log.info(
                            "Defence-regression probe end: name=%r variant=%s elapsed=%.2fs outcome=%s",
                            spec.name,
                            variant.variant_kind,
                            time.monotonic() - probe_start,
                            result.send_error or ("refused" if result.refused else "not_refused"),
                        )
        except TimeoutError:
            timed_out = True
            _log.warning(
                "Defence-regression pre-pass deadline reached: timeout=%.1fs completed=%d; remaining probes untested",
                timeout_seconds,
                len(results),
            )
        summary = DefenceRegressionRunSummary(
            specs_evaluated=evaluated_specs,
            variants_evaluated=len(results),
            variants_refused=sum(1 for r in results if r.send_error is None and r.refused),
            variants_failed=sum(r.send_error is not None for r in results),
            timed_out=timed_out,
        )
        _log.info(
            "Defence-regression pre-pass: %d spec(s), %d variant(s), "
            "%.0f%% correctly refused; failed=%d timed_out=%s elapsed=%.2fs",
            summary.specs_evaluated,
            summary.variants_evaluated,
            summary.paraphrase_coverage * 100,
            summary.variants_failed,
            summary.timed_out,
            time.monotonic() - started,
        )
        return results, summary

    async def _send_one(
        self,
        spec: DefenceRegressionSpec,
        variant: DefenceRegressionVariant,
        timeout_seconds: float = 30.0,
    ) -> DefenceRegressionResult:
        session = AttackSession(
            session_id=f"defence-regression-{uuid.uuid4().hex[:8]}",
            target_url=self._target_url,
            chain_id="defence-regression",
        )
        _log.info(
            "Defence-regression send: name=%r variant=%s session=%s timeout=%.2fs",
            spec.name,
            variant.variant_kind,
            session.session_id,
            timeout_seconds,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                response_text, _ = await self._client.send(
                    variant.message,
                    session,
                    retry_transient=True,
                )
        except asyncio.CancelledError:
            _log.info(
                "Defence-regression probe cancelled: name=%r variant=%s",
                spec.name,
                variant.variant_kind,
            )
            raise
        except Exception as exc:  # noqa: BLE001 — one bad send must not abort the pre-pass
            reason = (
                "probe_timeout"
                if isinstance(exc, TimeoutError)
                else f"send_error:{type(exc).__name__}"
            )
            _log.warning(
                "Defence-regression send failed for %r/%s: %s",
                spec.name,
                variant.variant_kind,
                reason,
            )
            return DefenceRegressionResult(
                variant=variant,
                severity=spec.severity,
                refused=False,
                send_error=reason,
            )
        outcome = classify_transport(response_text or "")
        transport_error = None
        if not (response_text or "").strip():
            transport_error = "empty_response"
        elif outcome != TransportOutcome.OK:
            transport_error = f"transport:{outcome.value}"
        elif error_only_response(response_text):
            transport_error = "error_envelope"
        if transport_error:
            return DefenceRegressionResult(
                variant=variant,
                severity=spec.severity,
                refused=False,
                send_error=transport_error,
            )
        refused = is_refusal(response_text or "")
        excerpt = (response_text or "")[:_EXCERPT_CHARS]
        evidence = (
            f"Regression {spec.name!r} ({variant.variant_kind}) was "
            f"{'refused' if refused else 'NOT refused'}."
        )
        return DefenceRegressionResult(
            variant=variant,
            severity=spec.severity,
            refused=refused,
            response_excerpt=excerpt,
            evidence=evidence,
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
