"""Judge-accuracy corpus harness — offline replay of LLMResponseEvaluator.

There was previously no way to measure whether the redteam judge
(``LLMResponseEvaluator`` in ``nuguard/redteam/llm_engine/response_evaluator.py``)
is actually accurate — no labeled corpus, no harness. The only number in the
repo was a hand-counted "19% likely false positives" figure in a one-off doc
(``documentation/docs/llm-runs/redteam-optimization.md``), not reproducible or
automated.

This module replays a frozen, hand-labeled corpus of
``(goal_type, payload, response, golden_data, expected_verdict)`` cases
(``tests/redteam/fixtures/judge_corpus/*.json``) through the real judge and
reports precision/recall/false-positive-rate per goal type, plus predicted-
vs-expected NGRS severity-band accuracy. It is a *measurement tool*, not a CI
gate — run it before/after judge or severity changes to see the effect, not
automatically on every push (opt-in via the ``redteam_judge_eval`` marker,
same pattern as the existing ``redteam_e2e`` marker).

The corpus deliberately includes the documented false-positive/false-negative
classes from git history: generic placeholder PII, refusal-then-echoed-value,
regulatory-framing-with-refusal, and the refuse-then-leak conflict that the
evidence-bundle refactor (see ``nuguard/redteam/llm_engine/evidence_bundle.py``)
specifically targets.

Usage::

    uv run pytest tests/redteam/test_judge_corpus.py -v -m redteam_judge_eval -s

Results are cached via the judge's own ``JudgeCache`` (see
``nuguard/redteam/llm_engine/judge_cache.py``), keyed on
``(goal_type, payload, response, golden_data)``, so re-running the harness
after a corpus edit only re-spends LLM calls on the changed/new cases.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest

from nuguard.common.llm_client import LLMClient
from nuguard.models.exploit_chain import GoalType
from nuguard.redteam.llm_engine.judge_cache import JudgeCache
from nuguard.redteam.llm_engine.response_evaluator import LLMResponseEvaluator
from nuguard.redteam.risk_engine import score_finding

_CORPUS_DIR = Path(__file__).parent / "fixtures" / "judge_corpus"
_CACHE_DIR = Path(__file__).resolve().parents[2] / "tests" / "output" / "judge_corpus_cache"

_HAS_LLM_KEY = bool(
    os.getenv("GEMINI_API_KEY") or os.getenv("LITELLM_API_KEY")
    or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
)


@dataclass
class CorpusCase:
    id: str
    goal_type: str
    payload: str
    response: str
    golden_data: str
    expected_succeeded: bool
    expected_confidence_band: str
    expected_severity_band: str
    notes: str = ""


@dataclass
class CaseResult:
    case: CorpusCase
    actual_succeeded: bool
    actual_confidence: str
    actual_severity_band: str

    @property
    def outcome(self) -> str:
        if self.case.expected_succeeded and self.actual_succeeded:
            return "TP"
        if not self.case.expected_succeeded and not self.actual_succeeded:
            return "TN"
        if not self.case.expected_succeeded and self.actual_succeeded:
            return "FP"
        return "FN"


def _load_corpus() -> list[CorpusCase]:
    cases: list[CorpusCase] = []
    for path in sorted(_CORPUS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        goal_type = data["goal_type"]
        for raw in data["cases"]:
            expected = raw["expected"]
            cases.append(
                CorpusCase(
                    id=raw["id"],
                    goal_type=goal_type,
                    payload=raw["payload"],
                    response=raw["response"],
                    golden_data=raw.get("golden_data", ""),
                    expected_succeeded=bool(expected["succeeded"]),
                    expected_confidence_band=expected.get("confidence_band", ""),
                    expected_severity_band=expected.get("severity_band", ""),
                    notes=raw.get("notes", ""),
                )
            )
    return cases


def _predicted_severity_band(goal_type: str, confidence: str) -> str:
    """Approximate the severity band the judge's output alone would produce.

    A goal-type-only NGRS score with the judge's confidence as the sole
    evidence input — this deliberately does NOT replicate the full
    `_build_findings` pipeline (canary/policy-violation context isn't
    available to a bare judge call), so treat this as an approximation of
    the judge's contribution to severity, not the final reported severity.
    """
    try:
        goal = GoalType(goal_type)
    except ValueError:
        return "unknown"
    result = score_finding(goal, llm_confidence=confidence or None)
    return result.severity.value


def _print_report(results: list[CaseResult]) -> None:
    by_goal: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_goal[r.case.goal_type].append(r)

    print("\n\n=== Judge corpus report ===")
    header = f"{'goal_type':<24}{'n':>4}{'TP':>4}{'FP':>4}{'TN':>4}{'FN':>4}{'precision':>10}{'recall':>8}{'fp_rate':>9}"
    print(header)
    print("-" * len(header))

    total_correct_sev = 0
    total_n = 0
    for goal_type, goal_results in sorted(by_goal.items()):
        tp = sum(1 for r in goal_results if r.outcome == "TP")
        fp = sum(1 for r in goal_results if r.outcome == "FP")
        tn = sum(1 for r in goal_results if r.outcome == "TN")
        fn = sum(1 for r in goal_results if r.outcome == "FN")
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        recall = tp / (tp + fn) if (tp + fn) else float("nan")
        fp_rate = fp / (fp + tn) if (fp + tn) else float("nan")
        print(
            f"{goal_type:<24}{len(goal_results):>4}{tp:>4}{fp:>4}{tn:>4}{fn:>4}"
            f"{precision:>10.2f}{recall:>8.2f}{fp_rate:>9.2f}"
        )

    print("\n=== Severity band accuracy (approximate — judge-confidence-only) ===")
    for r in results:
        total_n += 1
        if r.actual_severity_band == r.case.expected_severity_band:
            total_correct_sev += 1
    sev_acc = total_correct_sev / total_n if total_n else float("nan")
    print(f"Predicted-vs-expected severity band match: {total_correct_sev}/{total_n} ({sev_acc:.0%})")

    misses = [r for r in results if r.outcome in ("FP", "FN")]
    if misses:
        print("\n=== Misses ===")
        for r in misses:
            print(f"  [{r.outcome}] {r.case.id} ({r.case.goal_type}): {r.case.notes}")


@pytest.mark.redteam_judge_eval
@pytest.mark.asyncio
async def test_judge_corpus_accuracy() -> None:
    """Replay the judge corpus and report precision/recall/FP-rate per goal type.

    Advisory only — does not gate on accuracy thresholds (LLM judgement
    varies run to run even at temperature=0 across providers/models). Fails
    only on infrastructure problems: a configured LLM key that the judge
    can't actually reach (every case falling back to the canned/unavailable
    response would silently produce a report full of False negatives that
    looks like a judge regression when it's actually a config problem).
    """
    if not _HAS_LLM_KEY:
        pytest.skip(
            "No LLM API key configured (GEMINI_API_KEY / LITELLM_API_KEY / "
            "OPENAI_API_KEY / ANTHROPIC_API_KEY) — skipping judge corpus replay."
        )

    cases = _load_corpus()
    assert cases, f"No corpus cases found under {_CORPUS_DIR}"

    llm = LLMClient()
    cache = JudgeCache(cache_dir=_CACHE_DIR, sbom_key="judge-corpus")
    evaluator = LLMResponseEvaluator(llm, cache=cache)

    results: list[CaseResult] = []
    unavailable_count = 0
    for case in cases:
        verdict = await evaluator.evaluate(
            goal_type=case.goal_type,
            payload=case.payload,
            response=case.response,
            golden_data=case.golden_data,
        )
        if verdict.get("evidence") == "LLM evaluation unavailable":
            unavailable_count += 1
        confidence = str(verdict.get("confidence", ""))
        results.append(
            CaseResult(
                case=case,
                actual_succeeded=bool(verdict.get("succeeded")),
                actual_confidence=confidence,
                actual_severity_band=_predicted_severity_band(case.goal_type, confidence),
            )
        )

    _print_report(results)

    # Infrastructure sanity check only: a configured key that never actually
    # reaches the LLM (bad model name, auth failure, ...) would silently
    # produce a report that looks like the judge regressed to "never
    # succeeds" — fail loudly on that distinct failure mode instead.
    assert unavailable_count < len(cases), (
        "Every case fell back to the canned/unavailable response — the "
        "configured LLM client is not reachable, not a judge accuracy issue."
    )
