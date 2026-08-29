"""Pydantic-only public entry points for the behavior package.

These are thin async wrapper functions around :class:`BehaviorAnalyzer` and
:class:`BehaviorRunner` for callers outside the CLI: plain, JSON-serializable
request models in, JSON-serializable Pydantic result models out. The existing
classes, their constructors, and their methods are untouched — everything here
is additive.

Unlike :func:`nuguard.common.discovery.run_discovery` (a best-effort probe that
never raises), a full analysis/scenario run is not best-effort: these wrappers
do not catch or suppress exceptions. Errors such as ``TargetUnavailableError``
or auth failures propagate to the caller unchanged.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel

from nuguard.behavior.analyzer import BehaviorAnalyzer
from nuguard.behavior.models import (
    BehaviorAnalysisResult,
    BehaviorRunResult,
    BehaviorScenario,
)
from nuguard.behavior.runner import BehaviorRunner
from nuguard.common.discovery import DiscoveredProfile
from nuguard.common.logging import get_logger
from nuguard.common.stream_runtime import StreamRunHandle, create_stream_handle
from nuguard.common.streaming_models import (
    StreamDeltaPayload,
    StreamProgressPayload,
    StreamTerminalPayload,
)
from nuguard.config import BehaviorConfig
from nuguard.remediation.backfill import backfill_finding_remediation
from nuguard.remediation.models import RemediationArtefact

if TYPE_CHECKING:
    from pathlib import Path

    from nuguard.behavior.judge_cache import JudgeCache
    from nuguard.behavior.models import IntentProfile
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy, PolicyControl
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


class BehaviorAnalysisRequest(BaseModel):
    """JSON-safe input for :func:`analyze_behavior`."""

    config: BehaviorConfig
    mode: Literal["static", "dynamic", "static+dynamic", "minimal"] = "static+dynamic"


async def analyze_behavior(
    request: BehaviorAnalysisRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
    sbom_path: "Path | None" = None,
    policy: "CognitivePolicy | None" = None,
    controls: "list[PolicyControl] | None" = None,
    llm_client: "LLMClient | None" = None,
    remediation_llm_client: "LLMClient | None" = None,
) -> BehaviorAnalysisResult:
    """Run static+dynamic behavior analysis from a JSON-safe request.

    Thin wrapper around ``BehaviorAnalyzer(...).analyze(mode=...)``. ``sbom``,
    ``policy``, ``controls``, and ``llm_client`` stay as separate keyword
    arguments rather than request fields since they are large domain objects
    or live/stateful collaborators, not run configuration.
    """
    _log.debug("analyze_behavior: mode=%s", request.mode)
    analyzer = BehaviorAnalyzer(
        config=request.config,
        sbom=sbom,
        sbom_path=sbom_path,
        policy=policy,
        controls=controls,
        llm_client=llm_client,
        remediation_llm_client=remediation_llm_client,
    )
    return await analyzer.analyze(mode=request.mode)


class BehaviorRunRequest(BaseModel):
    """JSON-safe input for :func:`run_behavior_scenarios`."""

    config: BehaviorConfig
    scenarios: list[BehaviorScenario]
    pre_scan_profile: DiscoveredProfile | None = None


async def _synthesize_behavior_remediation_plan(
    findings: list[dict],
    *,
    sbom: "AiSbomDocument | None",
    policy: "CognitivePolicy | None",
    llm_client: "LLMClient | None",
) -> list[RemediationArtefact]:
    """Best-effort structured remediation for a plain list of finding dicts.

    Mirrors ``nuguard.redteam.public_api._build_remediation_plan`` (which
    reuses this same synthesizer for redteam findings): remediation synthesis
    enriches the result but must never fail the run, so exceptions are logged
    and swallowed. Returns ``[]`` when there is no SBOM or no findings to
    synthesize against.
    """
    if sbom is None or not findings:
        return []
    try:
        from nuguard.remediation.synthesizer import RemediationSynthesizer  # noqa: PLC0415

        synthesizer = RemediationSynthesizer(sbom=sbom, policy=policy, llm_client=llm_client)
        return await synthesizer.synthesize_findings_async(findings)
    except Exception as exc:  # noqa: BLE001
        _log.warning("run_behavior_scenarios: remediation synthesis failed: %s", exc)
        return []


async def run_behavior_scenarios(
    request: BehaviorRunRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
    sbom_path: "Path | None" = None,
    policy: "CognitivePolicy | None" = None,
    intent: "IntentProfile | None" = None,
    llm_client: "LLMClient | None" = None,
    remediation_llm_client: "LLMClient | None" = None,
    judge_cache: "JudgeCache | None" = None,
    _progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> BehaviorRunResult:
    """Run a list of behavior scenarios from a JSON-safe request.

    Thin wrapper around ``BehaviorRunner(...).run(...)``. Additionally
    synthesizes ``result.remediation_plan`` — concrete, SBOM-node-specific
    remediation artefacts — from the run's findings, the same way
    :meth:`~nuguard.behavior.analyzer.BehaviorAnalyzer.analyze` does for the
    full static+dynamic pipeline, and backfills each finding's flat
    ``remediation`` string from that plan. This is best-effort enrichment: it
    never raises, and simply leaves ``remediation_plan`` empty on failure.
    """
    _log.debug("run_behavior_scenarios: %d scenario(s)", len(request.scenarios))
    runner_kwargs: dict[str, Any] = {
        "config": request.config,
        "sbom": sbom,
        "sbom_path": sbom_path,
        "policy": policy,
        "intent": intent,
        "llm_client": llm_client,
        "judge_cache": judge_cache,
    }
    if _progress_sink is not None:
        runner_kwargs["progress_sink"] = _progress_sink
    runner = BehaviorRunner(**runner_kwargs)
    result = await runner.run(request.scenarios, pre_scan_profile=request.pre_scan_profile)
    result.remediation_plan = await _synthesize_behavior_remediation_plan(
        result.findings, sbom=sbom, policy=policy, llm_client=remediation_llm_client or llm_client
    )
    backfill_finding_remediation(result.findings, result.remediation_plan)
    return result


async def discover_behavior_profile(
    config: BehaviorConfig,
    *,
    sbom: "AiSbomDocument | None" = None,
    sbom_path: "Path | None" = None,
    policy: "CognitivePolicy | None" = None,
    intent: "IntentProfile | None" = None,
    llm_client: "LLMClient | None" = None,
) -> DiscoveredProfile | None:
    """Run pre-scan discovery from a JSON-safe config.

    Thin wrapper around ``BehaviorRunner(...).discover()``.
    """
    runner = BehaviorRunner(
        config=config, sbom=sbom, sbom_path=sbom_path, policy=policy, intent=intent, llm_client=llm_client,
    )
    return await runner.discover()


def _stream_finding_view(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": f.get("finding_id"),
        "title": f.get("title"),
        "severity": f.get("severity"),
        "goal_type": f.get("goal_type"),
        "scenario_type": f.get("scenario_type"),
    }


def analyze_behavior_static(result: BehaviorAnalysisResult) -> BehaviorAnalysisResult:
    """Return a stable static projection for external callers.

    This is currently identity-preserving to keep behavior stable while
    providing a public, named contract surface.
    """
    return result


async def analyze_behavior_stream(
    request: BehaviorRunRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
    policy: "CognitivePolicy | None" = None,
    intent: "IntentProfile | None" = None,
    llm_client: "LLMClient | None" = None,
    remediation_llm_client: "LLMClient | None" = None,
    judge_cache: "JudgeCache | None" = None,
) -> StreamRunHandle[BehaviorRunResult]:
    """Run behavior scenarios with a typed stream and final-result handle."""
    run_id = str(uuid.uuid4())

    async def _worker(controller):
        def _publish_progress(update: dict[str, Any]) -> None:
            kind = update.get("kind")
            total = int(update.get("scenarios_total") or 0)
            completed = int(update.get("scenarios_completed") or 0)
            payload = {
                "scenarios_total": total,
                "scenarios_completed": completed,
                "progress_pct": completed / total if total else 0.0,
                "current_scenario_type": update.get("scenario_type"),
                "scenario_id": update.get("scenario_id"),
                "scenario_title": update.get("scenario_title"),
                "scenario_status": update.get("scenario_status"),
            }
            if kind == "plan":
                controller.publish(
                    event_type="scenario_plan_ready",
                    phase="planning",
                    payload=StreamProgressPayload.model_validate(payload).model_dump(mode="json"),
                )
            elif kind == "scenario_started":
                controller.publish(
                    event_type="scenario_started",
                    phase="execution",
                    payload=StreamProgressPayload.model_validate(payload).model_dump(mode="json"),
                )
            elif kind == "scenario_finished":
                controller.publish(
                    event_type="scenario_progress",
                    phase="execution",
                    payload=StreamProgressPayload.model_validate(payload).model_dump(mode="json"),
                )
                turn_report_added = update.get("turn_report_added") or []
                if turn_report_added:
                    controller.publish(
                        event_type="findings_delta",
                        phase="execution",
                        payload=StreamDeltaPayload(
                            turn_report_added=turn_report_added,
                        ).model_dump(mode="json"),
                    )

        controller.publish(
            event_type="run_started",
            phase="init",
            payload={"scenario_count": len(request.scenarios)},
        )
        try:
            result = await run_behavior_scenarios(
                request,
                sbom=sbom,
                policy=policy,
                intent=intent,
                llm_client=llm_client,
                remediation_llm_client=remediation_llm_client,
                judge_cache=judge_cache,
                _progress_sink=_publish_progress,
            )
            controller.publish(
                event_type="findings_delta",
                phase="execution",
                payload=StreamDeltaPayload(
                    findings_added=[_stream_finding_view(f) for f in result.findings],
                ).model_dump(mode="json"),
            )
            controller.publish_terminal(
                event_type="completed",
                phase="finalize",
                payload=StreamTerminalPayload(
                    status="completed",
                    summary={
                        "scan_outcome": result.scan_outcome,
                        "findings_count": len(result.findings),
                        "scenarios_executed": result.scenarios_executed,
                    },
                ).model_dump(mode="json"),
            )
            controller.set_final_result(result)
        except asyncio.CancelledError as exc:
            controller.publish_terminal(
                event_type="failed",
                phase="finalize",
                payload=StreamTerminalPayload(
                    status="failed",
                    failure_stage="cancelled",
                    error_type=type(exc).__name__,
                    error_message="Behavior stream cancelled",
                ).model_dump(mode="json"),
            )
            controller.set_final_exception(exc)
            raise
        except Exception as exc:  # noqa: BLE001
            controller.publish_terminal(
                event_type="failed",
                phase="finalize",
                payload=StreamTerminalPayload(
                    status="failed",
                    summary={},
                    is_retryable=False,
                    failure_stage="run_behavior_scenarios",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ).model_dump(mode="json"),
            )
            controller.set_final_exception(exc)

    return create_stream_handle(run_id, _worker)
