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

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from nuguard.behavior.analyzer import BehaviorAnalyzer
from nuguard.behavior.models import (
    BehaviorAnalysisResult,
    BehaviorRunResult,
    BehaviorScenario,
    RemediationArtefact,
)
from nuguard.behavior.runner import BehaviorRunner
from nuguard.common.discovery import DiscoveredProfile
from nuguard.common.logging import get_logger
from nuguard.config import BehaviorConfig

if TYPE_CHECKING:
    from nuguard.behavior.judge_cache import JudgeCache
    from nuguard.behavior.models import IntentProfile
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy, PolicyControl
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


class BehaviorAnalysisRequest(BaseModel):
    """JSON-safe input for :func:`analyze_behavior`."""

    config: BehaviorConfig
    mode: Literal["static", "dynamic", "static+dynamic"] = "static+dynamic"


async def analyze_behavior(
    request: BehaviorAnalysisRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
    policy: "CognitivePolicy | None" = None,
    controls: "list[PolicyControl] | None" = None,
    llm_client: "LLMClient | None" = None,
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
        policy=policy,
        controls=controls,
        llm_client=llm_client,
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
        from nuguard.behavior.remediation import RemediationSynthesizer  # noqa: PLC0415

        synthesizer = RemediationSynthesizer(sbom=sbom, policy=policy, llm_client=llm_client)
        return await synthesizer.synthesize_findings_async(findings)
    except Exception as exc:  # noqa: BLE001
        _log.warning("run_behavior_scenarios: remediation synthesis failed: %s", exc)
        return []


async def run_behavior_scenarios(
    request: BehaviorRunRequest,
    *,
    sbom: "AiSbomDocument | None" = None,
    policy: "CognitivePolicy | None" = None,
    intent: "IntentProfile | None" = None,
    llm_client: "LLMClient | None" = None,
    judge_cache: "JudgeCache | None" = None,
) -> BehaviorRunResult:
    """Run a list of behavior scenarios from a JSON-safe request.

    Thin wrapper around ``BehaviorRunner(...).run(...)``. Additionally
    synthesizes ``result.remediation_plan`` — concrete, SBOM-node-specific
    remediation artefacts — from the run's findings, the same way
    :meth:`~nuguard.behavior.analyzer.BehaviorAnalyzer.analyze` does for the
    full static+dynamic pipeline. This is best-effort enrichment: it never
    raises, and simply leaves ``remediation_plan`` empty on failure.
    """
    _log.debug("run_behavior_scenarios: %d scenario(s)", len(request.scenarios))
    runner = BehaviorRunner(
        config=request.config,
        sbom=sbom,
        policy=policy,
        intent=intent,
        llm_client=llm_client,
        judge_cache=judge_cache,
    )
    result = await runner.run(request.scenarios, pre_scan_profile=request.pre_scan_profile)
    result.remediation_plan = await _synthesize_behavior_remediation_plan(
        result.findings, sbom=sbom, policy=policy, llm_client=llm_client
    )
    return result


async def discover_behavior_profile(
    config: BehaviorConfig,
    *,
    sbom: "AiSbomDocument | None" = None,
    policy: "CognitivePolicy | None" = None,
    intent: "IntentProfile | None" = None,
    llm_client: "LLMClient | None" = None,
) -> DiscoveredProfile | None:
    """Run pre-scan discovery from a JSON-safe config.

    Thin wrapper around ``BehaviorRunner(...).discover()``.
    """
    runner = BehaviorRunner(config=config, sbom=sbom, policy=policy, intent=intent, llm_client=llm_client)
    return await runner.discover()
