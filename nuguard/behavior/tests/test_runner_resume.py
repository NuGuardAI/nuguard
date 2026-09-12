"""Tests for BehaviorRunner checkpoint/resume support (issue #508)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nuguard.behavior.models import BehaviorRunResult, ScenarioResult
from nuguard.behavior.runner import BehaviorRunner
from nuguard.behavior.tests.test_runner import (
    _make_config,
    _make_intent,
    _make_mock_policy,
    _make_mock_sbom,
    _make_scenario,
)
from nuguard.common.run_checkpoint import (
    PartialRunError,
    RunCheckpoint,
    behavior_scenario_obj_signature,
)


def _canned_result(name: str) -> ScenarioResult:
    return ScenarioResult(
        scenario_id="canned-id",
        scenario_name=name,
        scenario_type="intent_happy_path",
        verdicts=[{"overall_score": 4.5, "verdict": "PASS", "agents_mentioned": [], "tools_mentioned": [], "deviations": []}],
        overall_score=4.5,
        coverage_pct=1.0,
        uncovered_agents=[],
        uncovered_tools=[],
        total_turns=1,
        coverage_turns=0,
        deviations=[],
    )


@pytest.mark.asyncio
async def test_run_checkpoints_completed_scenarios(tmp_path):
    """A configured prompt_cache_dir causes a checkpoint file to be written and deleted on success."""
    cfg = _make_config()
    cfg.prompt_cache_dir = str(tmp_path)
    runner = BehaviorRunner(
        config=cfg,
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    mock_client = AsyncMock()
    canned = _canned_result("s1")
    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
        patch.object(runner, "_run_scenario", new=AsyncMock(return_value=canned)),
    ):
        result = await runner.run(scenarios=[_make_scenario("s1")])

    assert isinstance(result, BehaviorRunResult)
    # Checkpoint deleted on full success — nothing left over in prompt_cache_dir.
    assert list(tmp_path.glob("behavior-checkpoint-*.json")) == []


@pytest.mark.asyncio
async def test_run_writes_checkpoint_and_raises_partial_run_error_on_crash(tmp_path):
    cfg = _make_config()
    cfg.prompt_cache_dir = str(tmp_path)
    runner = BehaviorRunner(
        config=cfg,
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )
    mock_client = AsyncMock()
    canned = _canned_result("s1")

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
        patch.object(runner, "_run_scenario", new=AsyncMock(return_value=canned)),
        patch.object(runner, "_build_coverage_map", side_effect=RuntimeError("post-processing exploded")),
    ):
        with pytest.raises(PartialRunError) as excinfo:
            await runner.run(scenarios=[_make_scenario("s1")])

    exc = excinfo.value
    assert isinstance(exc.cause, RuntimeError)
    assert exc.checkpoint_path is not None
    assert exc.checkpoint_path.exists()

    saved = RunCheckpoint(tmp_path, "behavior").load(exc.checkpoint_path)
    assert saved is not None
    assert saved["status"] == "aborted"
    assert len(saved["scenario_results"]) == 1
    assert saved["scenario_results"][0]["scenario_name"] == "s1"


@pytest.mark.asyncio
async def test_resume_skips_already_completed_scenario(tmp_path):
    """A scenario whose signature is in the checkpoint's completed_signatures is never re-run."""
    cfg = _make_config()
    cfg.prompt_cache_dir = str(tmp_path)
    sbom = _make_mock_sbom()
    policy = _make_mock_policy()

    scenario_done = _make_scenario("already_done")
    scenario_new = _make_scenario("still_pending")
    done_result = _canned_result("already_done")

    # Write a checkpoint as if "already_done" completed in a prior run.
    from nuguard.common.run_checkpoint import fingerprint
    key = fingerprint(sbom, policy)
    checkpoint = RunCheckpoint(tmp_path, "behavior")
    checkpoint_path = checkpoint.path_for(key)
    checkpoint.save(
        checkpoint_path,
        {
            "cache_key": key,
            "status": "aborted",
            "completed_signatures": [behavior_scenario_obj_signature(scenario_done)],
            "scenario_results": [done_result.model_dump(mode="json")],
        },
    )
    cfg.resume = str(checkpoint_path)

    runner = BehaviorRunner(config=cfg, sbom=sbom, policy=policy, intent=_make_intent(), llm_client=None)
    mock_client = AsyncMock()
    run_scenario_mock = AsyncMock(return_value=_canned_result("still_pending"))

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
        patch.object(runner, "_run_scenario", new=run_scenario_mock),
    ):
        result = await runner.run(scenarios=[scenario_done, scenario_new])

    # Only the pending scenario was actually dispatched.
    assert run_scenario_mock.await_count == 1
    names = {sr.scenario_name for sr in result.scenario_results}
    assert names == {"already_done", "still_pending"}
    # Checkpoint deleted on the now-fully-successful resumed run.
    assert not checkpoint_path.exists()


@pytest.mark.asyncio
async def test_resume_rejects_mismatched_fingerprint(tmp_path):
    cfg = _make_config()
    cfg.prompt_cache_dir = str(tmp_path)
    sbom = _make_mock_sbom()
    policy = _make_mock_policy()

    checkpoint = RunCheckpoint(tmp_path, "behavior")
    checkpoint_path = checkpoint.path_for("mismatched")
    checkpoint.save(checkpoint_path, {"cache_key": "0000000000000000", "scenario_results": []})
    cfg.resume = str(checkpoint_path)

    runner = BehaviorRunner(config=cfg, sbom=sbom, policy=policy, intent=_make_intent(), llm_client=None)
    mock_client = AsyncMock()

    from nuguard.common.run_checkpoint import CheckpointMismatchError

    with (
        patch.object(runner, "_build_client", new=AsyncMock(return_value=mock_client)),
        patch.object(runner, "_build_policy_evaluator", return_value=None),
    ):
        with pytest.raises(CheckpointMismatchError):
            await runner.run(scenarios=[_make_scenario("s1")])
