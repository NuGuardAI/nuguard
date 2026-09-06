"""Pydantic-only public entry point for the redteam package (v1 engine only).

A thin async wrapper around :class:`RedteamOrchestrator` for callers outside
    except asyncio.CancelledError:
        raise
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

import asyncio
import dataclasses
import uuid
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.logging import get_logger
from nuguard.common.run_checkpoint import PartialRunError, RunCheckpoint
from nuguard.common.stream_runtime import StreamRunHandle, create_stream_handle
from nuguard.common.streaming_models import (
    StreamDeltaPayload,
    StreamProgressPayload,
    StreamTerminalPayload,
)
from nuguard.config import AppAuthConfig, RedteamFindingTriggers
from nuguard.models.finding import Finding
from nuguard.models.health_report import TargetHealthReport
from nuguard.models.token_usage import TokenUsage
from nuguard.redteam.executor.orchestrator import (
    RedteamOrchestrator,
    _dedup_findings,
    finding_matches_scenario_filter,
)
from nuguard.redteam.target.canary import CanaryConfig
from nuguard.remediation.backfill import backfill_finding_remediation
from nuguard.remediation.models import RemediationArtefact

if TYPE_CHECKING:
    from pathlib import Path

    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy
    from nuguard.policy.public_api import CognitivePolicyParseResult
    from nuguard.redteam.catalog.coverage import CoverageReport
    from nuguard.redteam.target.log_reader import BufferLogReader, FileLogReader
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


def _protect_secret_strings(value: Any) -> Any:
    if isinstance(value, str):
        return SecretStr(value)
    if isinstance(value, dict):
        return {key: _protect_secret_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_protect_secret_strings(item) for item in value]
    return value


def _reveal_secret_strings(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: _reveal_secret_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_reveal_secret_strings(item) for item in value]
    return value


class RedteamLoginFlowConfig(BaseModel):
    """Secret-safe public login-flow configuration."""

    endpoint: str = "/login"
    method: Literal["POST", "GET"] = "POST"
    base_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    token_response_key: str = "access_token"
    token_header: str = "Authorization: Bearer"
    refresh_on_401: bool = True

    @field_validator("payload", mode="before")
    @classmethod
    def _protect_payload(cls, value: Any) -> Any:
        return _protect_secret_strings(value)

    def to_internal(self) -> LoginFlowConfig:
        """Convert to the runtime model immediately before target access."""
        return LoginFlowConfig(
            endpoint=self.endpoint,
            method=self.method,
            base_url=self.base_url,
            payload=_reveal_secret_strings(self.payload),
            token_response_key=self.token_response_key,
            token_header=self.token_header,
            refresh_on_401=self.refresh_on_401,
        )


class RedteamAuthConfig(BaseModel):
    """Secret-safe public authentication configuration for red-team runs."""

    type: Literal["bearer", "api_key", "basic", "none", "login_flow", "cookie_file"] = "none"
    header: SecretStr = Field(default_factory=lambda: SecretStr(""))
    username: SecretStr = Field(default_factory=lambda: SecretStr(""))
    password: SecretStr = Field(default_factory=lambda: SecretStr(""))
    login_flow: RedteamLoginFlowConfig | None = None
    cookie_file: str = ""

    @model_validator(mode="after")
    def _validate_fields(self) -> "RedteamAuthConfig":
        if self.type in ("bearer", "api_key") and not self.header.get_secret_value():
            raise ValueError(f"auth.type={self.type} requires auth.header")
        if self.type == "basic" and not (
            self.username.get_secret_value() and self.password.get_secret_value()
        ):
            raise ValueError("auth.type=basic requires auth.username and auth.password")
        if self.type == "login_flow" and self.login_flow is None:
            raise ValueError("auth.type=login_flow requires auth.login_flow block")
        if self.type == "cookie_file" and not self.cookie_file:
            raise ValueError("auth.type=cookie_file requires auth.cookie_file")
        return self

    def to_internal(self) -> AuthConfig:
        """Convert to the existing runtime model without retaining a plaintext dump."""
        return AuthConfig(
            type=self.type,
            header=self.header.get_secret_value(),
            username=self.username.get_secret_value(),
            password=self.password.get_secret_value(),
            login_flow=self.login_flow.to_internal() if self.login_flow else None,
            cookie_file=self.cookie_file,
        )


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
    extra_headers: dict[str, SecretStr] | None = None
    strict_outcome: bool = False
    scenario_filter: list[str] | None = None
    canary_config: CanaryConfig | None = None
    auth_config: RedteamAuthConfig | None = None
    finding_triggers: RedteamFindingTriggers | None = None
    verbose: bool = False
    credentials: dict[str, SecretStr] | None = None
    scenario_timeout: float = 180.0
    turn_delay_seconds: float = 5.0
    scenario_delay_seconds: float = 0.0
    similar_miss_threshold: int = 4
    hard_refusal_abort_turns: int = 5
    stall_abort_threshold: int = 8
    skip_discovery: bool = False
    discovery_max_turns: int = 3
    capability_discovery: bool = True
    chat_payload_extras: dict[str, Any] | None = None
    pre_run_warmup: int = 0
    verify_findings: bool = True
    golden_data: dict[str, Any] | None = None
    suppress_spa_html_auth_bypass: bool = True
    codegen_escalation_enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalize_public_auth_config(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(
            data.get("auth_config"), (AppAuthConfig, AuthConfig)
        ):
            normalized = dict(data)
            normalized["auth_config"] = data["auth_config"].model_dump()
            return normalized
        return data
    mode: str = "concurrent"
    progressive_halt_on_severity: str = "none"
    probe_llm: bool = False
    resume_from: str | None = None
    """Path to a checkpoint file written by a previous aborted run (see
    ``prompt_cache_dir``). Already-completed scenarios are skipped and the
    final result combines the checkpointed and newly-run scenarios/findings.
    """


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
        "aborted_endpoint_unreachable",
        "inconclusive_target_errors",
        "no_findings",
        "partial",
    ]
    config_notes: list[str] = Field(default_factory=list)
    llm_executive_summary: str | None = None
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
    ``remediation_llm_client`` (falling back to ``eval_llm``) is supplied to
    :func:`run_redteam`. Empty when synthesis fails or no SBOM is available.
    """
    security_invariants: list[dict[str, Any]] = Field(default_factory=list)
    """Phase-0 pass/fail criteria derived from the Cognitive Policy (see
    nuguard.redteam.invariants.derive_security_invariants and
    docs/claude-redteam-3.md §3). Always populated regardless of ``mode`` —
    cheap to compute, and the report only renders the section when non-empty.
    """


class RedteamExecutionResult(RedteamRunResult):
    """Final result model returned by the streaming redteam API."""


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
        from nuguard.remediation.synthesizer import RemediationSynthesizer  # noqa: PLC0415

        synthesizer = RemediationSynthesizer(sbom=sbom, policy=policy, llm_client=llm_client)
        finding_dicts = [
            {
                "finding_id": f.finding_id,
                "title": f.title,
                "description": f.description or "",
                "affected_component": f.affected_component or "unknown",
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                "goal_type": f.goal_type or "",
                "scenario_type": f.scenario_type or "",
                "evidence_quote": f.evidence_quote or "",
                "reasoning": f.reasoning or "",
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


def _build_partial_result(orchestrator: RedteamOrchestrator, exc: PartialRunError) -> RedteamRunResult:
    """Build a JSON-safe partial :class:`RedteamRunResult` from a :class:`PartialRunError`.

    Used when ``orchestrator.run()`` aborted after >=1 scenario completed —
    see :meth:`RedteamOrchestrator.run` and issue #508. Remediation synthesis
    and the LLM coding brief are skipped (best-effort extras, not worth the
    extra LLM calls on an already-failed run); everything else mirrors the
    success-path result construction below.
    """
    payload = exc.partial_payload
    findings = _dedup_findings([Finding(**f) for f in payload.get("findings", [])])
    coverage_tracker = getattr(orchestrator, "_coverage_tracker", None)
    return RedteamRunResult(
        findings=findings,
        scenario_records=payload.get("scenario_records", []),
        scan_outcome="partial",
        config_notes=list(orchestrator.config_notes),
        llm_executive_summary=orchestrator.llm_executive_summary,
        llm_coding_brief=None,
        scenarios_run=orchestrator.scenarios_run,
        input_tokens_used=orchestrator.input_tokens_used,
        output_tokens_used=orchestrator.output_tokens_used,
        token_usage=orchestrator.token_usage,
        health_report=getattr(orchestrator, "health_report", None),
        resolved_chat_path=orchestrator.resolved_chat_path,
        resolved_chat_path_source=orchestrator.resolved_chat_path_source,
        catalog_coverage=_catalog_coverage_to_dict(getattr(orchestrator, "catalog_coverage", None)),
        coverage_tracker=coverage_tracker.to_dict() if coverage_tracker is not None else None,
        remediation_plan=[],
        security_invariants=[i.model_dump() for i in getattr(orchestrator, "security_invariants", [])],
    )


async def run_redteam(
    request: RedteamRunRequest,
    *,
    sbom: "AiSbomDocument",
    sbom_path: "Path | None" = None,
    policy: "CognitivePolicy | CognitivePolicyParseResult | None" = None,
    policy_controls: "list | None" = None,
    redteam_llm: "LLMClient | None" = None,
    eval_llm: "LLMClient | None" = None,
    remediation_llm_client: "LLMClient | None" = None,
    app_log_reader: "FileLogReader | BufferLogReader | None" = None,
    catalog: "tuple | None" = None,
    log_path: "Path | None" = None,
    prompt_cache_dir: "Path | None" = None,
    _progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> RedteamRunResult:
    """Run a full v1 red-team scan from a JSON-safe request.

    Thin wrapper around ``RedteamOrchestrator(...).run()``. Mirrors the CLI's
    ``_run_orchestrator`` exactly: the same litellm-client cleanup in
    ``finally``, and the same post-run ``scenario_filter`` re-filtering of
    findings (duplicated rather than extracted into a shared helper, to keep
    the CLI file untouched by this change).
    """
    _log.debug("run_redteam: target_url=%s profile=%s", request.target_url, request.profile)
    from pathlib import Path as _Path  # noqa: PLC0415

    from nuguard.policy.public_api import normalize_cognitive_policy  # noqa: PLC0415

    normalized_policy = normalize_cognitive_policy(policy)

    _resume_checkpoint: dict[str, Any] | None = None
    _resume_checkpoint_path: "Path | None" = None
    if request.resume_from:
        _resume_checkpoint_path = _Path(request.resume_from)
        _checkpoint_dir = prompt_cache_dir or _resume_checkpoint_path.parent
        _resume_checkpoint = RunCheckpoint(_checkpoint_dir, "redteam").load(_resume_checkpoint_path)
        if _resume_checkpoint is None:
            raise ValueError(f"--resume checkpoint not found or unreadable: {request.resume_from}")

    orchestrator = RedteamOrchestrator(
        sbom=sbom,
        target_url=request.target_url,
        sbom_path=sbom_path,
        policy=normalized_policy,
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
        extra_headers=_reveal_secret_strings(request.extra_headers),
        strict_outcome=request.strict_outcome,
        scenario_filter=request.scenario_filter,
        auth_config=request.auth_config.to_internal() if request.auth_config else None,
        finding_triggers=request.finding_triggers,
        verbose=request.verbose,
        credentials=_reveal_secret_strings(request.credentials),
        scenario_timeout=request.scenario_timeout,
        turn_delay_seconds=request.turn_delay_seconds,
        scenario_delay_seconds=request.scenario_delay_seconds,
        similar_miss_threshold=request.similar_miss_threshold,
        hard_refusal_abort_turns=request.hard_refusal_abort_turns,
        stall_abort_threshold=request.stall_abort_threshold,
        skip_discovery=request.skip_discovery,
        discovery_max_turns=request.discovery_max_turns,
        capability_discovery=request.capability_discovery,
        chat_payload_extras=request.chat_payload_extras,
        catalog=catalog,
        pre_run_warmup=request.pre_run_warmup,
        verify_findings=request.verify_findings,
        golden_data=request.golden_data,
        suppress_spa_html_auth_bypass=request.suppress_spa_html_auth_bypass,
        codegen_escalation_enabled=request.codegen_escalation_enabled,
        mode=request.mode,
        progressive_halt_on_severity=request.progressive_halt_on_severity,
        progress_sink=_progress_sink,
        probe_llm=request.probe_llm,
        resume_checkpoint=_resume_checkpoint,
        resume_checkpoint_path=_resume_checkpoint_path,
    )

    try:
        try:
            findings = await orchestrator.run()
        except PartialRunError as exc:
            exc.partial_result = _build_partial_result(orchestrator, exc)
            raise
    finally:
        try:
            from litellm.llms.custom_httpx.async_client_cleanup import (
                close_litellm_async_clients,  # noqa: PLC0415
            )

            await close_litellm_async_clients()
        except Exception:  # noqa: BLE001
            pass

    # Defensive re-check of scenario_filter against the returned findings —
    # the orchestrator's own pre-run filtering (_scenario_matches_filter,
    # which checks goal_type, scenario_type, AND title) is what actually
    # decides which scenarios execute; this only needs to be consistent with
    # it. Previously checked goal_type alone, so a scenario-type-level
    # filter (e.g. "APPROVAL_STATE_FORGERY") would pass the orchestrator's
    # pre-run filter but then have its resulting finding silently dropped
    # here, since the token is never a substring of the finding's goal_type.
    if request.scenario_filter:
        _filters = {s.strip().lower().replace("-", "_") for s in request.scenario_filter if s and s.strip()}
        findings = [f for f in findings if finding_matches_scenario_filter(f, _filters)]

    remediation_plan = await _build_remediation_plan(
        findings,
        sbom=sbom,
        policy=normalized_policy,
        llm_client=remediation_llm_client or eval_llm,
    )
    backfill_finding_remediation(findings, remediation_plan)

    llm_coding_brief: str | None = None
    if eval_llm and findings:
        try:
            from nuguard.redteam.llm_engine.summary_generator import (
                LLMSummaryGenerator,  # noqa: PLC0415
            )

            llm_coding_brief = await LLMSummaryGenerator(eval_llm).coding_agent_brief(findings)
        except Exception as exc:  # noqa: BLE001
            _log.warning("run_redteam: coding agent brief generation failed: %s", exc)

    coverage_tracker = getattr(orchestrator, "_coverage_tracker", None)
    return RedteamRunResult(
        findings=findings,
        scenario_records=[dataclasses.asdict(r) for r in orchestrator.scenario_records],
        scan_outcome=orchestrator.scan_outcome,  # type: ignore[arg-type]
        config_notes=list(orchestrator.config_notes),
        llm_executive_summary=orchestrator.llm_executive_summary,
        llm_coding_brief=llm_coding_brief,
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
        security_invariants=[i.model_dump() for i in getattr(orchestrator, "security_invariants", [])],
    )


def _stream_finding_view(f: Finding) -> dict[str, Any]:
    return {
        "finding_id": f.finding_id,
        "title": f.title,
        "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
        "goal_type": f.goal_type,
        "scenario_type": f.scenario_type,
    }


async def run_redteam_stream(
    request: RedteamRunRequest,
    *,
    sbom: "AiSbomDocument",
    policy: "CognitivePolicy | CognitivePolicyParseResult | None" = None,
    policy_controls: "list | None" = None,
    redteam_llm: "LLMClient | None" = None,
    eval_llm: "LLMClient | None" = None,
    remediation_llm_client: "LLMClient | None" = None,
    app_log_reader: "FileLogReader | BufferLogReader | None" = None,
    catalog: "tuple | None" = None,
    log_path: "Path | None" = None,
    prompt_cache_dir: "Path | None" = None,
) -> StreamRunHandle[RedteamExecutionResult]:
    """Run redteam with a typed event stream and final-result handle."""
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
                "current_goal_type": update.get("goal_type"),
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
                findings_added = update.get("findings_added") or []
                if findings_added:
                    controller.publish(
                        event_type="findings_delta",
                        phase="execution",
                        payload=StreamDeltaPayload(
                            findings_added=[_stream_finding_view(finding) for finding in findings_added],
                        ).model_dump(mode="json"),
                    )

        controller.publish(
            event_type="run_started",
            phase="init",
            payload={"target_url": request.target_url, "profile": request.profile},
        )
        try:
            result = await run_redteam(
                request,
                sbom=sbom,
                policy=policy,
                policy_controls=policy_controls,
                redteam_llm=redteam_llm,
                eval_llm=eval_llm,
                remediation_llm_client=remediation_llm_client,
                app_log_reader=app_log_reader,
                catalog=catalog,
                log_path=log_path,
                prompt_cache_dir=prompt_cache_dir,
                _progress_sink=_publish_progress,
            )
            controller.publish(
                event_type="findings_delta",
                phase="finalize",
                payload=StreamDeltaPayload(
                    scenario_record_added=result.scenario_records,
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
                        "scenarios_run": result.scenarios_run,
                    },
                ).model_dump(mode="json"),
            )
            controller.set_final_result(RedteamExecutionResult.model_validate(result.model_dump(mode="json")))
        except asyncio.CancelledError:
            raise
        except PartialRunError as exc:
            partial = exc.partial_result
            # event_type stays "failed" (not "partial") — _StreamController.events()
            # only closes the stream on "completed"/"failed" (nuguard/common/stream_runtime.py);
            # "partial" is carried in the payload's status field instead, and the
            # final_result below is still the salvaged partial RedteamExecutionResult.
            controller.publish_terminal(
                event_type="failed",
                phase="finalize",
                payload=StreamTerminalPayload(
                    status="partial",
                    summary={
                        "scan_outcome": "partial",
                        "findings_count": len(partial.findings) if partial is not None else 0,
                        "checkpoint_path": str(exc.checkpoint_path) if exc.checkpoint_path else None,
                        "error": str(exc.cause),
                    },
                ).model_dump(mode="json"),
            )
            if partial is not None:
                controller.set_final_result(
                    RedteamExecutionResult.model_validate(partial.model_dump(mode="json"))
                )
            raise

    return create_stream_handle(run_id, _worker)
