"""Rate-limit probe wiring in the orchestrator (issue #766). Offline only."""
from __future__ import annotations

import pytest

from nuguard.config import RedteamRateLimitPolicy
from nuguard.models.exploit_chain import ScenarioType
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.executor.orchestrator import (
    RedteamOrchestrator,
    _rate_limit_chain_status,
)
from nuguard.redteam.executor.rate_limit_eval import RateLimitEvaluation
from nuguard.redteam.report import _scenario_outcome
from nuguard.redteam.scenarios.api_attacks import build_rate_limit_probe
from nuguard.sbom.models import AiSbomDocument

POLICY = RedteamRateLimitPolicy(max_requests=3, window_seconds=10.0)


def _orch(policy=None) -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://target.test",
        concurrency=1,
        rate_limit_policy=policy,
    )


def _results(scenario, statuses, step=0.1, retries=None):
    retries = retries or {}
    out = []
    for i, (st, status) in enumerate(zip(scenario.chain.steps, statuses)):
        sr = StepResult(step=st, response="{}", tool_calls=[], http_status_code=status)
        sr.http_observation = {
            "sent_at": i * step,
            "retries_429": retries.get(i, 0),
            "headers": {"retry-after": "30"} if status == 429 else {},
        }
        out.append(sr)
    return out


def _run(orch, statuses, **kw):
    scenario = build_rate_limit_probe(endpoint_id="ep", endpoint_name="Login", path="/login")
    srs = _results(scenario, statuses, **kw)
    ev = orch._rate_limit_evaluation(scenario, srs)
    findings = orch._build_findings(
        scenario, scenario.chain, srs, orch._build_step_details(srs), rate_limit_eval=ev
    )
    return scenario, ev, findings


def test_original_defect_five_200s_without_policy_is_not_a_finding() -> None:
    scenario, ev, findings = _run(_orch(), [200] * 5)
    assert findings == []
    assert ev is not None and ev.verdict == "inconclusive"
    assert ev.reason == "no_rate_limit_policy"


def test_no_policy_probe_is_reported_inconclusive_not_defended() -> None:
    _, ev, _ = _run(_orch(), [200] * 5)
    status = _rate_limit_chain_status(ev, False, "completed")
    assert status == "inconclusive:no_rate_limit_policy"

    class _Rec:
        chain_status = status
        had_finding = False
        steps: list[dict] = [{}]
        turns_used = 5

    assert _scenario_outcome(_Rec()) == "inconclusive"


def test_policy_exceeded_without_enforcement_is_one_deterministic_finding() -> None:
    scenario, ev, findings = _run(_orch(POLICY), [200] * 5)
    assert ev.verdict == "finding"
    assert len(findings) == 1
    f = findings[0]
    assert f.scenario_type == ScenarioType.RESOURCE_EXHAUSTION
    assert "policy_exceeded_without_enforcement" in ev.reason
    assert "5 requests were accepted" in f.evidence


def test_enforcement_at_policy_limit_is_defended_with_no_finding() -> None:
    _, ev, findings = _run(_orch(POLICY), [200, 200, 200, 429, 429])
    assert findings == []
    assert ev.verdict == "defended"
    assert _rate_limit_chain_status(ev, False, "completed") == "completed"


def test_burst_inside_policy_is_inconclusive() -> None:
    _, ev, findings = _run(_orch(RedteamRateLimitPolicy(max_requests=6, window_seconds=60)), [200] * 5)
    assert findings == []
    assert ev.reason == "insufficient_observations"


def test_policy_above_scanner_budget_is_inconclusive_not_defended() -> None:
    _, ev, findings = _run(_orch(RedteamRateLimitPolicy(max_requests=100, window_seconds=60)), [200] * 5)
    assert findings == []
    assert ev.reason == "policy_exceeds_scanner_budget"
    assert _rate_limit_chain_status(ev, False, "completed") == "inconclusive:policy_exceeds_scanner_budget"


def test_hidden_429_retry_is_defended_not_a_finding() -> None:
    _, ev, findings = _run(_orch(POLICY), [200] * 5, retries={3: 1})
    assert findings == []
    assert ev.verdict == "defended"


def test_non_probe_scenarios_are_not_evaluated() -> None:
    scenario = build_rate_limit_probe(endpoint_id="ep", endpoint_name="Login", path="/login")
    scenario.title = "Something Else"
    assert _orch(POLICY)._rate_limit_evaluation(scenario, []) is None


@pytest.mark.parametrize(
    ("evaluation", "had_finding", "status", "expected"),
    [
        (None, False, "completed", "completed"),
        (RateLimitEvaluation("inconclusive", "transport_error", ""), False, "completed", "inconclusive:transport_error"),
        (RateLimitEvaluation("defended", "enforcement_observed", ""), False, "completed", "completed"),
        (RateLimitEvaluation("finding", "x", ""), True, "completed", "completed"),
        (RateLimitEvaluation("finding", "x", ""), False, "completed", "inconclusive:finding_trigger_disabled"),
        (RateLimitEvaluation("inconclusive", "no_timing", ""), False, "timeout", "timeout"),
        (RateLimitEvaluation("inconclusive", "no_timing", ""), False, "aborted:circuit", "aborted:circuit"),
    ],
)
def test_chain_status_rewrite_rules(evaluation, had_finding, status, expected) -> None:
    assert _rate_limit_chain_status(evaluation, had_finding, status) == expected
