"""Pydantic-only public entry point for the redteam package (v1 engine only).

A thin async wrapper around :class:`RedteamOrchestrator` for callers outside
the CLI: a plain, JSON-serializable request model in, a JSON-serializable
result model out. ``RedteamOrchestrator`` itself and its constructor and
``run()`` are untouched — everything here is additive. The CLI's
``nuguard.cli.commands.redteam._run_orchestrator`` calls :func:`run_redteam`
directly for the v1 engine, so this module is the single source of truth for
v1 scan execution and remediation-plan synthesis, not a parallel copy of it.

Scope: only the v1 :class:`RedteamOrchestrator` engine is wrapped.
``RedteamV2Orchestrator`` (the v2 engine path) is out of scope.

Like :mod:`nuguard.behavior.public_api`, this does not catch or suppress
exceptions raised while running a scan (e.g. ``TargetUnavailableError``,
``AuthError``) — a full scan is not a best-effort probe. Only the LiteLLM
async-client cleanup is guaranteed via ``finally``, mirroring the CLI exactly.
"""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from nuguard.behavior.models import RemediationArtefact
from nuguard.common.auth import AuthConfig
from nuguard.common.logging import get_logger
from nuguard.config import RedteamFindingTriggers
from nuguard.models.finding import Finding
from nuguard.models.health_report import TargetHealthReport
from nuguard.models.token_usage import TokenUsage
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.target.canary import CanaryConfig

if TYPE_CHECKING:
    from pathlib import Path

    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy
    from nuguard.redteam.catalog.coverage import CoverageReport
    from nuguard.redteam.target.log_reader import BufferLogReader, FileLogReader
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


class RedteamRunRequest(BaseModel):
    """JSON-safe input for :func:`run_redteam`.

    Mirrors ``RedteamOrchestrator.__init__``'s scalar and config-object
    parameters with identical defaults. Large domain objects (``sbom``,
    ``policy``), live/stateful collaborators (``redteam_llm``, ``eval_llm``,
    ``app_log_reader``), and objects that may hold compiled callables
    (``policy_controls``, ``catalog``) are intentionally kept OUT of this
    model and passed as separate keyword arguments to :func:`run_redteam`
    instead — the same separation :mod:`nuguard.common.discovery` uses for
    ``client``/``session``.
    """

    target_url: str
    profile: str = "ci"
    min_impact_score: float = 0.0
    chat_path: str = "/chat"
    chat_payload_key: str = "message"
    chat_payload_list: bool = False
    chat_response_key: str | None = None
    concurrency: int = RedteamOrchestrator.DEFAULT_CONCURRENCY
    request_timeout: float = 120.0
    guided_conversations: bool = True
    guided_max_turns: int = 12
    guided_concurrency: int = 3
    guided_mutation_mode: str = "hard"
    tree_breadth: int = 0
    tree_max_depth: int = 0
    extra_headers: dict[str, str] | None = None
    strict_outcome: bool = False
    scenario_filter: list[str] | None = None
    canary_config: CanaryConfig | None = None
    auth_config: AuthConfig | None = None
    finding_triggers: RedteamFindingTriggers | None = None
    verbose: bool = False
    credentials: dict[str, str] | None = None
    scenario_timeout: float = 180.0
    turn_delay_seconds: float = 5.0
    scenario_delay_seconds: float = 0.0
    similar_miss_threshold: int = 4
    skip_discovery: bool = False
    discovery_max_turns: int = 3
    chat_payload_extras: dict[str, Any] | None = None
    pre_run_warmup: int = 0
    verify_findings: bool = False
    golden_data: dict[str, Any] | None = None
    suppress_spa_html_auth_bypass: bool = True
    codegen_escalation_enabled: bool = True


class RedteamRunResult(BaseModel):
    """JSON-safe result of :func:`run_redteam`.

    Bundles ``RedteamOrchestrator.run()``'s return value together with the
    side-effect instance attributes the CLI reads separately today (via its
    12-tuple return from ``_run_orchestrator``), so external callers get one
    self-contained object instead of having to know which attributes to read.
    """

    findings: list[Finding]
    scenario_records: list[dict[str, Any]]
    scan_outcome: Literal[
        "critical_findings",
        "high_findings",
        "findings",
        "aborted_target_unavailable",
        "inconclusive_target_errors",
        "no_findings",
    ]
    config_notes: list[str] = Field(default_factory=list)
    llm_executive_summary: str | None = None
    llm_remediations: dict[str, str] = Field(default_factory=dict)
    llm_coding_brief: str | None = None
    scenarios_run: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    token_usage: TokenUsage
    health_report: TargetHealthReport | None = None
    resolved_chat_path: str
    resolved_chat_path_source: str
    catalog_coverage: dict[str, Any] | None = None
    coverage_tracker: dict[str, Any] | None = None
    remediation_plan: list[RemediationArtefact] = Field(default_factory=list)
    """Concrete, SBOM-node-specific remediation artefacts for ``findings``.

    Synthesized best-effort from ``findings`` via ``RemediationSynthesizer``
    (see :func:`_build_remediation_plan` below) — the same synthesis the CLI's
    ``nuguard redteam`` command relies on, since ``_run_orchestrator`` calls
    :func:`run_redteam` directly. Populated with contextual LLM patch text when
    ``eval_llm`` is supplied to :func:`run_redteam`. Empty when synthesis fails
    or no SBOM is available.
    """


async def _build_remediation_plan(
    findings: list[Finding],
    *,
    sbom: "AiSbomDocument | None",
    policy: "CognitivePolicy | None",
    llm_client: "LLMClient | None",
) -> list[RemediationArtefact]:
    """Synthesize per-SBOM-node remediation artefacts from redteam findings.

    Uses ``synthesize_findings_async`` rather than the sync
    ``synthesize_findings`` since this runs inside ``run_redteam``'s
    already-running event loop, so LLM patch calls need to be awaited
    directly rather than silently skipped by the sync shim.
    Best-effort: returns ``[]`` on missing SBOM, no findings, or any failure.
    """
    if sbom is None or not findings:
        return []
    try:
        from nuguard.behavior.remediation import RemediationSynthesizer  # noqa: PLC0415

        synthesizer = RemediationSynthesizer(sbom=sbom, policy=policy, llm_client=llm_client)
        finding_dicts = [
            {
                "finding_id": f.finding_id,
                "title": f.title,
                "description": f.description or "",
                "affected_component": f.affected_component or "unknown",
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                "goal_type": f.goal_type or "",
            }
            for f in findings
        ]
        return await synthesizer.synthesize_findings_async(finding_dicts)
    except Exception as exc:  # noqa: BLE001
        _log.warning("run_redteam: remediation synthesis failed — skipping plan: %s", exc)
        return []


def _catalog_coverage_to_dict(report: "CoverageReport | None") -> dict[str, Any] | None:
    """Convert a ``CoverageReport`` dataclass to a JSON-safe dict.

    ``dataclasses.asdict`` leaves ``Enum``-typed list fields
    (``categories_covered``, ``capabilities_detected``) as enum members
    rather than plain strings, so those two fields are normalized separately.
    """
    if report is None:
        return None
    d = dataclasses.asdict(report)
    d["categories_covered"] = [c.value for c in report.categories_covered]
    d["capabilities_detected"] = [c.value for c in report.capabilities_detected]
    return d


async def run_redteam(
    request: RedteamRunRequest,
    *,
    sbom: "AiSbomDocument",
    policy: "CognitivePolicy | None" = None,
    policy_controls: "list | None" = None,
    redteam_llm: "LLMClient | None" = None,
    eval_llm: "LLMClient | None" = None,
    app_log_reader: "FileLogReader | BufferLogReader | None" = None,
    catalog: "tuple | None" = None,
    log_path: "Path | None" = None,
    prompt_cache_dir: "Path | None" = None,
) -> RedteamRunResult:
    """Run a full v1 red-team scan from a JSON-safe request.

    Thin wrapper around ``RedteamOrchestrator(...).run()``. Mirrors the CLI's
    ``_run_orchestrator`` exactly: the same litellm-client cleanup in
    ``finally``, and the same post-run ``scenario_filter`` re-filtering of
    findings (duplicated rather than extracted into a shared helper, to keep
    the CLI file untouched by this change).
    """
    _log.debug("run_redteam: target_url=%s profile=%s", request.target_url, request.profile)
    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url=request.target_url,
        policy=policy,
        policy_controls=policy_controls,
        canary_config=request.canary_config,
        profile=request.profile,
        min_impact_score=request.min_impact_score,
        log_path=log_path,
        chat_path=request.chat_path,
        chat_payload_key=request.chat_payload_key,
        chat_payload_list=request.chat_payload_list,
        chat_response_key=request.chat_response_key,
        concurrency=request.concurrency,
        request_timeout=request.request_timeout,
        redteam_llm=redteam_llm,
        eval_llm=eval_llm,
        prompt_cache_dir=prompt_cache_dir,
        app_log_reader=app_log_reader,
        guided_conversations=request.guided_conversations,
        guided_max_turns=request.guided_max_turns,
        guided_concurrency=request.guided_concurrency,
        guided_mutation_mode=request.guided_mutation_mode,
        tree_breadth=request.tree_breadth,
        tree_max_depth=request.tree_max_depth,
        extra_headers=request.extra_headers,
        strict_outcome=request.strict_outcome,
        scenario_filter=request.scenario_filter,
        auth_config=request.auth_config,
        finding_triggers=request.finding_triggers,
        verbose=request.verbose,
        credentials=request.credentials,
        scenario_timeout=request.scenario_timeout,
        turn_delay_seconds=request.turn_delay_seconds,
        scenario_delay_seconds=request.scenario_delay_seconds,
        similar_miss_threshold=request.similar_miss_threshold,
        skip_discovery=request.skip_discovery,
        discovery_max_turns=request.discovery_max_turns,
        chat_payload_extras=request.chat_payload_extras,
        catalog=catalog,
        pre_run_warmup=request.pre_run_warmup,
        verify_findings=request.verify_findings,
        golden_data=request.golden_data,
        suppress_spa_html_auth_bypass=request.suppress_spa_html_auth_bypass,
        codegen_escalation_enabled=request.codegen_escalation_enabled,
    )

    try:
        findings = await orchestrator.run()
    finally:
        try:
            from litellm.llms.custom_httpx.async_client_cleanup import (
                close_litellm_async_clients,  # noqa: PLC0415
            )

            await close_litellm_async_clients()
        except Exception:  # noqa: BLE001
            pass

    # Mirrors nuguard/cli/commands/redteam.py:_run_orchestrator's post-run
    # scenario_filter re-application verbatim.
    if request.scenario_filter:
        findings = [
            f
            for f in findings
            if not f.goal_type
            or any(
                s.lower().replace("-", "_") in (f.goal_type or "").lower()
                for s in request.scenario_filter
            )
        ]

    remediation_plan = await _build_remediation_plan(
        findings, sbom=sbom, policy=policy, llm_client=eval_llm
    )

    coverage_tracker = getattr(orchestrator, "_coverage_tracker", None)
    return RedteamRunResult(
        findings=findings,
        scenario_records=[dataclasses.asdict(r) for r in orchestrator.scenario_records],
        scan_outcome=orchestrator.scan_outcome,  # type: ignore[arg-type]
        config_notes=list(orchestrator.config_notes),
        llm_executive_summary=orchestrator.llm_executive_summary,
        llm_remediations=orchestrator.llm_remediations,
        llm_coding_brief=orchestrator.llm_coding_brief,
        scenarios_run=orchestrator.scenarios_run,
        input_tokens_used=orchestrator.input_tokens_used,
        output_tokens_used=orchestrator.output_tokens_used,
        token_usage=orchestrator.token_usage,
        health_report=getattr(orchestrator, "health_report", None),
        resolved_chat_path=orchestrator.resolved_chat_path,
        resolved_chat_path_source=orchestrator.resolved_chat_path_source,
        catalog_coverage=_catalog_coverage_to_dict(getattr(orchestrator, "catalog_coverage", None)),
        coverage_tracker=coverage_tracker.to_dict() if coverage_tracker is not None else None,
        remediation_plan=remediation_plan,
    )
