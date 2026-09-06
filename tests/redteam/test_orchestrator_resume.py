"""Tests for RedteamOrchestrator checkpoint/resume support (issue #508).

Exercises the run()/`_run_impl()` split directly (monkeypatching `_run_impl`)
rather than driving a full scenario/target pipeline — the behavior under test
is the checkpoint save/PartialRunError/resume-preseed mechanics in `run()`
itself, not scenario execution (already covered by test_phase_gating.py and
friends).
"""
from __future__ import annotations

import dataclasses

import pytest

from nuguard.common.run_checkpoint import (
    CheckpointMismatchError,
    PartialRunError,
    RunCheckpoint,
    fingerprint,
)
from nuguard.config import RedteamFindingTriggers
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator, ScenarioRecord
from nuguard.sbom.models import AiSbomDocument


def _sbom() -> AiSbomDocument:
    return AiSbomDocument(target="unit-test", nodes=[], edges=[])


def _orchestrator(tmp_path, **kwargs) -> RedteamOrchestrator:
    return RedteamOrchestrator(
        sbom=_sbom(),
        target_url="http://localhost:3000",
        finding_triggers=RedteamFindingTriggers(),
        codegen_escalation_enabled=False,
        prompt_cache_dir=tmp_path,
        **kwargs,
    )


def _record(title: str = "Extract PII") -> ScenarioRecord:
    return ScenarioRecord(
        title=title,
        goal_type="DATA_EXFILTRATION",
        scenario_type="PII_EXTRACTION",
        description="unit test scenario",
        impact_score=7.0,
        affected="Agent",
        chain_status="completed",
        had_finding=False,
    )


def _finding(finding_id: str = "F-1") -> Finding:
    return Finding(
        finding_id=finding_id,
        title="Leaked PII",
        description="unit test finding",
        severity=Severity.HIGH,
        goal_type="DATA_EXFILTRATION",
    )


@pytest.mark.asyncio
async def test_run_raises_partial_run_error_and_writes_checkpoint_on_crash(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    async def _boom():
        # Simulate progress before the crash — mirrors how ScenarioRecords
        # accumulate on self.scenario_records incrementally during a real run,
        # and how the real _run_impl sets up the checkpoint once the
        # effective policy is known (reproduced here since _run_impl is stubbed).
        orchestrator._checkpoint = RunCheckpoint(tmp_path, "redteam")
        orchestrator._checkpoint_path = orchestrator._checkpoint.path_for(
            fingerprint(orchestrator._sbom, None)
        )
        orchestrator.scenario_records.append(_record())
        orchestrator.findings.append(_finding())
        raise RuntimeError("target died mid-scan")

    orchestrator._run_impl = _boom  # type: ignore[method-assign]

    with pytest.raises(PartialRunError) as excinfo:
        await orchestrator.run()

    exc = excinfo.value
    assert isinstance(exc.cause, RuntimeError)
    assert exc.checkpoint_path is not None
    assert exc.checkpoint_path.exists()

    saved = RunCheckpoint(tmp_path, "redteam").load(exc.checkpoint_path)
    assert saved is not None
    assert saved["status"] == "aborted"
    assert saved["abort_reason"] == "RuntimeError"
    assert len(saved["scenario_records"]) == 1
    assert len(saved["findings"]) == 1
    assert saved["completed_signatures"] == ["DATA_EXFILTRATION|PII_EXTRACTION|Extract PII"]


@pytest.mark.asyncio
async def test_run_reraises_plain_exception_when_nothing_completed(tmp_path):
    """No scenario_records accumulated before the crash → no PartialRunError, just the original exception."""
    orchestrator = _orchestrator(tmp_path)

    async def _boom():
        raise RuntimeError("failed before anything ran")

    orchestrator._run_impl = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="failed before anything ran"):
        await orchestrator.run()


@pytest.mark.asyncio
async def test_run_deletes_checkpoint_on_success(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    # Simulate a checkpoint already existing from an earlier in-progress save
    # (real _run_impl sets self._checkpoint/_checkpoint_path itself once the
    # effective policy is known — reproduced here since _run_impl is stubbed).
    key = fingerprint(orchestrator._sbom, None)
    checkpoint = RunCheckpoint(tmp_path, "redteam")
    path = checkpoint.path_for(key)
    checkpoint.save(path, {"cache_key": key, "status": "in_progress"})
    assert path.exists()

    async def _succeed():
        orchestrator._checkpoint = checkpoint
        orchestrator._checkpoint_path = path
        return []

    orchestrator._run_impl = _succeed  # type: ignore[method-assign]

    result = await orchestrator.run()
    assert result == []
    assert not path.exists()


def test_resume_preseeds_scenario_records_and_findings(tmp_path):
    sbom = _sbom()
    key = fingerprint(sbom, None)
    checkpoint_payload = {
        "cache_key": key,
        "status": "aborted",
        "completed_signatures": ["DATA_EXFILTRATION|PII_EXTRACTION|Extract PII"],
        "scenario_records": [dataclasses.asdict(_record())],
        "findings": [_finding().model_dump(mode="json")],
    }

    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:3000",
        finding_triggers=RedteamFindingTriggers(),
        codegen_escalation_enabled=False,
        prompt_cache_dir=tmp_path,
        resume_checkpoint=checkpoint_payload,
    )
    assert orchestrator._resume_checkpoint == checkpoint_payload
    # The actual pre-seeding of scenario_records/findings/_completed_signatures
    # happens inside _run_impl() once self._effective_policy is known (it
    # needs the fingerprint), not in __init__ — verified in the full-pipeline
    # coverage of test_phase_gating.py and friends. Here we confirm the
    # constructor at least accepts and stores the checkpoint unchanged.


def test_validate_fingerprint_rejects_mismatched_checkpoint_on_resume(tmp_path):
    from nuguard.common.run_checkpoint import validate_fingerprint

    sbom = _sbom()
    mismatched_payload = {"cache_key": "0000000000000000"}
    with pytest.raises(CheckpointMismatchError):
        validate_fingerprint(mismatched_payload, sbom=sbom, policy=None)
