"""build_rate_limit_probe must only observe; the verdict is deterministic (#766)."""
from __future__ import annotations

import copy

from nuguard.models.exploit_chain import ScenarioType
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.executor.orchestrator import _is_destructive_scenario
from nuguard.redteam.executor.rate_limit_eval import is_rate_limit_probe
from nuguard.redteam.scenarios.api_attacks import build_rate_limit_probe


def _probe():
    return build_rate_limit_probe(
        endpoint_id="ep-1", endpoint_name="Login", path="/login", method="POST"
    )


def test_probe_is_classified_resource_exhaustion() -> None:
    sc = _probe()
    assert sc.scenario_type == ScenarioType.RESOURCE_EXHAUSTION
    assert sc.chain.scenario_type == ScenarioType.RESOURCE_EXHAUSTION


def test_probe_burst_size_is_unchanged() -> None:
    assert len(_probe().chain.steps) == 5


def test_probe_steps_are_observation_only() -> None:
    for step in _probe().chain.steps:
        assert step.success_signal == ""
        assert step.use_llm_eval is False
        assert step.on_failure == "skip"
        assert step.contributes_to_finding is False
        assert step.target_path == "/login"


def test_http_200_alone_is_not_a_success_signal() -> None:
    step = _probe().chain.steps[-1]
    result = StepResult(
        step=step, response='{"status": "ok"}', tool_calls=[], http_status_code=200
    )
    assert result.success_signal_found is False


def test_probe_stays_destructive_so_gating_is_preserved() -> None:
    assert _is_destructive_scenario(_probe())


def test_is_rate_limit_probe_matches_only_the_probe() -> None:
    sc = _probe()
    assert is_rate_limit_probe(sc)
    other = copy.deepcopy(sc)
    other.title = "Resource Exhaustion — TestAgent"
    assert not is_rate_limit_probe(other)
